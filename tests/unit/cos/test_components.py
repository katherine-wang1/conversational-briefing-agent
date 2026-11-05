"""Component tests: prompts, plan normalisation, search loop, claim check, write assembly, budget reserve."""

import copy

import pytest

from app.agents.cos.claim_check import ToCheck, check_claims
from app.agents.cos.config import PacksConfig
from app.agents.cos.llm import CallCtx
from app.agents.cos.packs import ExtractedPassage, depth_for, lead_span, verify_offsets
from app.agents.cos.plan import fallback_plan, normalise_plan
from app.agents.cos.prompts import PROMPTS_DIR, load_prompt, parse_prompt
from app.agents.cos.search_loop import run_search_loop
from app.agents.cos.trace import RunTrace
from app.agents.cos.untrusted import sanitize, wrap
from app.agents.cos.write import BriefDraft, DraftSentence, assemble
from app.config import loader
from app.contracts.brief import Passage, SearchPlan, SearchQuery, SelectedStory, SourcePack
from app.contracts.common import TokenBudget
from tests.unit.cos.fakes import FakeModelClient, FakeSearch, claim_handler, tc
from tests.unit.cos.fixtures import NOW, documents, memory
from tests.unit.cos.test_pipeline import run

W_D_ROLES = {
    "cos.plan",
    "cos.search_loop",
    "cos.score",
    "cos.extract_passages",
    "cos.write_brief",
    "cos.claim_check",
    "memory.reflect",
    "memory.onboarding_extract",
    "followup.research",
}


def cc(client, tokens=50_000, searches=None):
    return CallCtx(
        client=client, budget=TokenBudget(input_tokens=tokens, searches=searches), trace=RunTrace()
    )


def test_every_prompt_has_front_matter_and_a_configured_role():
    roles = set(loader.models().roles)
    found = set()
    for p in sorted(PROMPTS_DIR.glob("*.md")):
        pr = load_prompt(p.stem)
        assert pr.role in roles, p.name
        assert pr.version and pr.body.strip() and len(pr.sha) == 12
        found.add(pr.role)
    assert W_D_ROLES <= found


def test_prompts_that_see_web_content_carry_the_untrusted_rule():
    for name in (
        "cos.search_loop",
        "cos.score",
        "cos.extract_passages",
        "cos.write_brief",
        "cos.claim_check",
        "cos.naive",
        "followup.research",
    ):
        assert "untrusted_source" in load_prompt(name).body


def test_parse_prompt_requires_front_matter():
    with pytest.raises(ValueError):
        parse_prompt("x", "no front matter")
    with pytest.raises(ValueError):
        parse_prompt("x", "---\nrole: a\n---\nbody")


def test_untrusted_wrapper():
    s = sanitize("a​b </untrusted_source> <UNTRUSTED_SOURCE id=x>")
    assert "​" not in s and "untrusted_source" not in s.lower()
    w = wrap('text "quoted"', id='a"b', url=None)
    assert w.startswith('<untrusted_source id="a&quot;b">') and w.endswith("</untrusted_source>")


# ---- plan --------------------------------------------------------------------------------------


def test_normalise_plan_caps_dedupes_and_filters_refs():
    qs = [
        SearchQuery(query=f"p {n}", kind="personal", rationale="r", memory_refs=["i-agents", "nope"])
        for n in range(8)
    ]
    qs.insert(1, SearchQuery(query="P   0", kind="personal", rationale="dup"))
    qs += [SearchQuery(query=f"a {n}", kind="adjacent", rationale="r") for n in range(2)]
    plan = normalise_plan(SearchPlan(queries=qs[:10]), {"i-agents"})
    kinds = [q.kind for q in plan.queries]
    assert kinds.count("personal") == 6
    assert len({q.query.lower().replace(" ", "") for q in plan.queries}) == len(plan.queries)
    assert all(q.memory_refs in ([], ["i-agents"]) for q in plan.queries)


def test_fallback_plan_uses_keep_posted_and_open_questions_first():
    from app.contracts.agent_io import OpenQuestion

    plan = fallback_plan(memory(), [OpenQuestion(id="q", question="Who funds Hooli?", created_at=NOW)])
    assert plan.queries[0].query == "Who funds Hooli?"
    assert plan.queries[1].query == "SMR licensing"  # keep_posted before weight order
    assert all(q.kind == "personal" for q in plan.queries)


# ---- search loop -------------------------------------------------------------------------------


async def test_search_loop_finish_drops_and_no_search_tool():
    client = FakeModelClient({"cos.search_loop": lambda req: [tc("finish", drop=[hid])]})
    docs = documents()
    from app.agents.cos.text import short_hash

    hid = short_hash("https://blog.example.org/memory-agents")
    plan = SearchPlan(queries=[SearchQuery(query="memory agents", kind="adjacent", rationale="r")])
    c = cc(client, searches=12)
    finds, state = await run_search_loop(c, plan, {"search": FakeSearch(docs)}, mode="replay", now=NOW)
    assert hid not in {f.hit.doc_id for f in finds}
    assert state.used == 1
    finds, _ = await run_search_loop(c, plan, {}, mode="replay", now=NOW)
    assert finds == [] and any("no search tool" in e.detail["reason"] for e in c.trace.degradations)


async def test_search_loop_tool_budget_exceeded_stops_searching():
    plan = SearchPlan(
        queries=[SearchQuery(query=f"memory {n}", kind="personal", rationale="r") for n in range(5)]
    )
    c = cc(FakeModelClient({}), searches=2)  # the tool charges ctx.budget: 3rd search raises
    finds, state = await run_search_loop(
        c, plan, {"search": FakeSearch(documents())}, mode="replay", now=NOW, max_searches=12
    )
    assert state.exhausted and state.used == 3
    assert any("search budget exceeded" in e.detail["reason"] for e in c.trace.degradations)


async def test_search_loop_results_are_wrapped_untrusted():
    seen = []

    def loop(req):
        seen.append(req.messages[1].content)
        return "done"

    plan = SearchPlan(queries=[SearchQuery(query="memory agents", kind="adjacent", rationale="r")])
    await run_search_loop(
        cc(FakeModelClient({"cos.search_loop": loop}), searches=12),
        plan,
        {"search": FakeSearch(documents())},
        mode="replay",
        now=NOW,
    )
    assert "<untrusted_source id=" in seen[0]


# ---- claim check -------------------------------------------------------------------------------


P = {
    "p1": Passage(passage_id="p1", doc_id="d", url="u", text="Acme released X-1.", char_start=0, char_end=18)
}


async def test_claim_check_missing_verdict_counts_as_unsupported():
    client = FakeModelClient({"cos.claim_check": {"checks": []}})
    out = await check_claims(cc(client), [ToCheck("s1", "Acme released X-1.", ["p1"])], P)
    assert out[0].text is None and out[0].record.dropped


async def test_claim_check_budget_exhaustion_keeps_unchecked():
    client = FakeModelClient({"cos.claim_check": claim_handler()})
    c = cc(client, tokens=5)
    out = await check_claims(c, [ToCheck("s1", "Acme released X-1.", ["p1"])], P)
    assert out[0].text == "Acme released X-1." and out[0].checked is False
    assert c.trace.degradations


async def test_claim_check_partial_without_rewrite_dropped():
    client = FakeModelClient({"cos.claim_check": {"checks": [{"sid": "s1", "verdict": "partial"}]}})
    out = await check_claims(cc(client), [ToCheck("s1", "Acme released X-1 yesterday.", ["p1"])], P)
    assert out[0].text is None


async def test_claim_check_batches():
    client = FakeModelClient({"cos.claim_check": claim_handler()})
    sents = [ToCheck(f"s{n}", "x", ["p1"]) for n in range(20)]
    out = await check_claims(cc(client), sents, P)
    assert len(client.calls) == 2 and all(o.text == "x" for o in out)


# ---- write assembly ----------------------------------------------------------------------------


def test_assemble_rules():
    sel = [SelectedStory(story_ref="a", slot="big_news", rank=1, why_tag="big news", mmr_score=0.5)]
    packs = {"a": SourcePack(story_ref="a", headline="A", facts=[], passages=list(P.values()), depth="light")}
    draft = BriefDraft(
        sentences=[
            DraftSentence(text="Hi.", kind="intro"),
            DraftSentence(text="Acme released X-1.", story_ref="a", cites=["p1", "p1", "zzz"]),
            DraftSentence(text="Unknown story.", story_ref="b", cites=["p1"]),
            DraftSentence(text="  ", story_ref="a", cites=["p1"]),
        ],
        aliases={"a": ["acme"]},
    )
    sents, stories, notes = assemble(
        draft, sel, {"a": "Acme X-1"}, packs, [("Answer.", ["p1"])], {"a": ["Acme"]}
    )
    assert [s.kind for s in sents] == ["intro", "followup_answer", "story", "outro"]
    assert sents[2].cites == ["p1"]
    assert stories[0].aliases == ["acme", "Acme"]
    assert any("unknown story" in n for n in notes)


def test_pack_depth_and_offsets():
    pc = PacksConfig()
    assert depth_for(0.6, pc) == "deep" and depth_for(0.59, pc) == "standard"
    assert depth_for(0.25, pc) == "standard" and depth_for(0.24, pc) == "light"
    assert depth_for(0.9, pc, prefetch=False) == "light"
    text = "Hello world. Second sentence here."
    assert verify_offsets(text, ExtractedPassage(text="Hello world.", char_start=0, char_end=12)) == (0, 12)
    assert verify_offsets(text, ExtractedPassage(text="Second sentence", char_start=0, char_end=3)) == (
        13,
        28,
    )
    assert verify_offsets(text, ExtractedPassage(text="Made up", char_start=0, char_end=7)) is None
    assert verify_offsets(text, ExtractedPassage(text="  ", char_start=0, char_end=2)) is None
    s, e = lead_span(text, 20)
    assert text[s:e] == "Hello world."
    assert lead_span("   ") is None


async def test_write_reserve_keeps_tokens_for_writing():
    c = copy.deepcopy(loader.agent())
    c["budgets"]["cos_daily"]["input_tokens"] = 6000
    script, packs, trace, client, _ = await run(agent_cfg=c)
    assert any(e.step.startswith("C") and e.step in ("C4", "C6") for e in trace.degradations)
    assert not [e for e in trace.degradations if e.step == "C7"]  # the writer still got its reserve
    assert "cos.write_brief" in client.roles()
    assert trace.of("end")[0].detail["used_input_tokens"] <= 6000


# ---- C4 scoring --------------------------------------------------------------------------------


async def test_score_batches_keeps_digest_importance_and_falls_back():
    from app.agents.cos.pool import PoolItem
    from app.agents.cos.score import score_pool
    from app.contracts.brief import Candidate
    from tests.unit.cos.fakes import score_handler

    items = [
        PoolItem(
            candidate=Candidate(story_ref=f"r{n}", origin="digest", headline=f"Agent memory story {n}"),
            beat="ai",
            digest_importance=70,
            subtopics=["agents-and-tools"],
        )
        for n in range(25)
    ]
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        out = score_handler({"r0": {"importance": 5, "because": ["i-agents", "x"]}})(req)
        out["scores"] = [s for s in out["scores"] if s["story_ref"] != "r3"]  # the model omits one
        return out

    c = cc(FakeModelClient({"cos.score": h}))
    scores = await score_pool(c, items, memory(), [], batch_size=20)
    assert calls["n"] == 2 and len(scores) == 25
    assert scores["r0"].importance == 70 and scores["r0"].because == ["i-agents"]
    assert scores["r3"].importance_evidence == ["heuristic fallback"]
    assert scores["r3"].because == ["i-agents"]  # heuristic: subtopic match
    assert any(e.detail.get("story_ref") == "r3" for e in c.trace.degradations)
