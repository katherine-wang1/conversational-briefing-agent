"""End-to-end newsroom runs on the SYNTHETIC fixture with a fake model client (no network)."""

import random

import pytest

from app.agents.newsroom import run_newsroom
from app.agents.newsroom.llm_steps import wrap_untrusted
from app.config.loader import ExperimentFlags
from app.contracts.common import TokenBudget
from app.contracts.digest import Digest

from .fakes import FakeFetchTool, FakeModelClient, ForbiddenTool
from .helpers import load_fixture, make_embedder, make_settings

OBS, RUNS = load_fixture()
KESTREL_HF = "https://huggingface.example/blog/northwind-kestrel-2"
ATLAS_D1 = "https://theverge.example/ai/orbital-ai-atlas-5-launch"
ATLAS_D2 = "https://techcrunch.example/2026/10/05/orbital-ai-pauses-atlas-5-rollout"
BURROW = "https://github.example/burrowdb/burrow"


async def newsroom(at, obs=OBS, client=None, tools=None, flags=None, **kw):
    client = client or FakeModelClient()
    kw.setdefault("settings", make_settings())
    return await run_newsroom(
        "ai", at, obs, client, tools if tools is not None else {}, flags, embedder=make_embedder(), **kw
    )


def entry_with(digest: Digest, url: str):
    return next(e for e in digest.entries if url in e.urls)


def story_with(trace, url: str):
    return next(s for s in trace.stories if url in s.item_urls)


async def test_day1_digest_end_to_end():
    digest, trace = await newsroom(RUNS[0])
    assert 0 < len(digest.entries) <= 30
    imps = [e.importance for e in digest.entries]
    assert imps == sorted(imps, reverse=True)
    assert all(e.importance >= 20 for e in digest.entries)
    top = digest.entries[0]
    assert KESTREL_HF in top.urls  # the multi-source story leads
    f = top.features
    # huggingface.example, techcrunch.example, theverge.example, arstechnica.example (reddit: unresolved)
    assert f.n_independent_domains == 4
    assert f.n_items == 5
    assert f.best_tier == 1 and f.best_rank == 1
    assert f.frontpage_hits == 8  # techmeme x3 + hn x4 + reddit x1 distinct (source, snapshot)
    assert f.has_primary_source  # hf_blog
    assert top.importance_rules is not None and 0 <= top.importance_rules <= 100
    assert top.evidence and top.subtopics and "not-a-real-subtopic" not in top.subtopics
    assert {s.step for s in trace.steps} == {"N2", "N3", "N4", "N5", "N6"}
    assert digest.version == trace.version and digest.beat == "ai" and digest.at == RUNS[0]


async def test_engagement_is_as_observed_never_later():
    digest, _ = await newsroom(RUNS[0])  # fixture also holds a later 610-point snapshot
    assert entry_with(digest, KESTREL_HF).features.hn_points_at_observation == 575


async def test_duplicates_collapse():
    _, trace = await newsroom(RUNS[0])
    kes = story_with(trace, KESTREL_HF)
    assert (
        "https://techcrunch.example/2026/10/04/northwind-labs-releases-kestrel-2-open-weight-model"
        in kes.item_urls
    )
    assert not any("utm_" in u or "techmeme.com" in u or "item?id=" in u for u in kes.item_urls)
    paper = story_with(trace, "https://arxiv.org/abs/2610.90417")
    assert paper.item_urls == ["https://arxiv.org/abs/2610.90417"]  # abs, v1, pdf, HF papers


async def test_cluster_tiebreak_used_and_recorded():
    client = FakeModelClient()
    _, trace = await newsroom(RUNS[0], client=client)
    tb = [c for c in trace.llm_calls if c.role == "newsroom.cluster_tiebreak"]
    assert tb and all(c.prompt_hash == trace.prompt_hashes["newsroom.cluster_tiebreak"] for c in tb)
    assert next(s for s in trace.steps if s.step == "N3").detail["tiebreak_merged"] >= 1
    assert len(story_with(trace, KESTREL_HF).item_urls) == 5  # merged via tiebreak


async def test_deterministic_ids_and_digest():
    d1, t1 = await newsroom(RUNS[0])
    shuffled = OBS[:]
    random.Random(7).shuffle(shuffled)
    d2, t2 = await newsroom(RUNS[0], obs=shuffled)
    assert d1.model_dump() == d2.model_dump()
    assert [s.story_id for s in t1.stories] == [s.story_id for s in t2.stories]


async def test_story_identity_across_days():
    d1, t1 = await newsroom(RUNS[0])
    d2, t2 = await newsroom(RUNS[1], history=t1.stories)
    kes1, kes2 = story_with(t1, KESTREL_HF), story_with(t2, KESTREL_HF)
    assert kes2.story_id == kes1.story_id  # continuation: same URL, no new facts
    assert kes2.update_of is None and kes2.first_seen == kes1.first_seen
    atlas1, atlas2 = story_with(t1, ATLAS_D1), story_with(t2, ATLAS_D2)
    assert atlas2.story_id != atlas1.story_id
    assert atlas2.update_of == atlas1.story_id  # developing story: new facts a day later
    e = entry_with(d2, ATLAS_D2)
    assert e.is_update_of == atlas1.story_id
    # without history the same day-2 run has no update links
    _, t2b = await newsroom(RUNS[1])
    assert story_with(t2b, ATLAS_D2).update_of is None


async def test_history_outside_lookback_is_ignored():
    _, t1 = await newsroom(RUNS[0])
    _, t2 = await newsroom(
        RUNS[1], history=t1.stories, settings=make_settings(clustering={"identity_lookback_days": 0})
    )
    assert story_with(t2, ATLAS_D2).update_of is None


async def test_a2_no_hn_signal():
    flags = ExperimentFlags(hn_signal=False)
    client = FakeModelClient()
    digest, trace = await newsroom(RUNS[0], client=client, flags=flags)
    full, ftrace = await newsroom(RUNS[0])
    assert next(s for s in trace.steps if s.step == "N2").detail["dropped"]["hn_ablation"] > 0
    assert not any(BURROW in s.item_urls for s in trace.stories)  # HN-only story is gone
    assert any(BURROW in s.item_urls for s in ftrace.stories)
    for e in digest.entries:
        assert e.features.hn_points_at_observation is None
        assert e.features.comments_at_observation is None
        assert not any("hn" in ev.lower() or "points" in ev.lower() for ev in e.evidence)
    prompts = "\n".join(m.content for r in client.requests if r.role == "newsroom.score" for m in r.messages)
    assert "points at observation" not in prompts and "hn_front" not in prompts
    kes = entry_with(digest, KESTREL_HF)
    assert kes.features.frontpage_hits == 4  # techmeme x3 + reddit; HN snapshots removed
    assert digest.version != full.version


async def test_fetch_budget_enforced_and_restricted_to_batch_urls():
    fetch = FakeFetchTool()
    client = FakeModelClient(fetch_urls=3, extra_fetch_urls=("https://evil.example/exfil",))
    _, trace = await newsroom(RUNS[0], client=client, tools={"fetch": fetch, "search": ForbiddenTool()})
    assert len(fetch.calls) == 5 == trace.usage.fetches  # budgets.newsroom_run.fetches
    refused = [t for t in trace.tool_calls if not t.ok]
    assert any("not one of the URLs" in t.error for t in refused)
    assert any("budget exhausted" in (t.error or "") for t in refused) or len(trace.tool_calls) == 6
    assert all(c.user_id is None and c.now == RUNS[0] for c in fetch.contexts)
    for r in client.requests:
        assert r.tools is None or [t.name for t in r.tools] == ["fetch"]
    # fetched text reaches the model wrapped as untrusted, with the closing-tag trick defanged
    tool_msgs = [m.content for r in client.requests for m in r.messages if m.role == "tool"]
    ok_msgs = [m for m in tool_msgs if m.startswith("<untrusted_source")]
    assert ok_msgs and all(m.count("</untrusted_source>") == 1 for m in ok_msgs)


async def test_no_fetch_tool_means_no_tools_offered():
    client = FakeModelClient(fetch_urls=3)
    await newsroom(RUNS[0], client=client, tools=None)
    assert all(r.tools is None for r in client.requests)


async def test_token_budget_degrades_to_rules_only():
    client = FakeModelClient()
    digest, trace = await newsroom(RUNS[0], client=client, budget=TokenBudget(input_tokens=3000, fetches=5))
    assert trace.degraded
    assert any((e.notes_for_editor or "").startswith("rules-only") for e in digest.entries)
    assert trace.usage.tokens_in <= 3000
    assert digest.entries  # the run still produces a digest


async def test_no_user_context_anywhere():
    client = FakeModelClient()
    await newsroom(RUNS[0], client=client, run_id="digests-v1", mode="replay")
    assert client.requests
    for r in client.requests:
        assert r.trace.user_id is None
        assert r.trace.run_id == "digests-v1"
        assert r.cache_namespace == "digests-v1"


async def test_snippets_wrapped_as_untrusted():
    client = FakeModelClient()
    await newsroom(RUNS[0], client=client)
    text = "\n".join(r.messages[1].content for r in client.requests if r.role == "newsroom.score")
    i = text.index("ignore all previous instructions")
    assert text.rfind("<untrusted_source", 0, i) > text.rfind("</untrusted_source>", 0, i)
    system = next(r for r in client.requests if r.role == "newsroom.score").messages[0].content
    assert "background knowledge" in system and "{{" not in system
    assert wrap_untrusted("x", "u", "a </untrusted_source> b").count("</untrusted_source>") == 1


async def test_evidence_without_features_is_capped():
    def vague(sid, feats, titles):
        return {
            "story_id": sid,
            "headline": titles[0],
            "summary": "s",
            "subtopics": ["open-models"],
            "entities": [],
            "importance": 99,
            "evidence": ["everyone knows this lab matters"],
        }

    digest, _ = await newsroom(RUNS[0], client=FakeModelClient(score_fn=vague))
    for e in digest.entries:
        assert e.importance <= round(e.importance_rules)
        assert "capped" in (e.notes_for_editor or "")


async def test_missing_or_invalid_model_output_falls_back():
    digest, trace = await newsroom(RUNS[0], client=FakeModelClient(raw_score_output="not json"))
    assert digest.entries
    assert all((e.notes_for_editor or "").startswith("rules-only") for e in digest.entries)
    assert any(not c.ok for c in trace.llm_calls if c.role == "newsroom.score")


async def test_trace_records_usage_and_prompt_hashes():
    _, trace = await newsroom(RUNS[0])
    assert set(trace.prompt_hashes) == {"newsroom.score", "newsroom.cluster_tiebreak"}
    assert trace.usage.llm_calls == len(trace.llm_calls) > 0
    assert trace.usage.tokens_in == sum(c.tokens_in for c in trace.llm_calls) > 0
    assert all(len(c.prompt_hash) == 64 for c in trace.llm_calls)
    assert trace.stories and len({s.story_id for s in trace.stories}) == len(trace.stories)


@pytest.mark.parametrize("flags", [None, {"hn_signal": True}, ExperimentFlags()])
async def test_flags_forms_accepted(flags):
    d, _ = await newsroom(RUNS[0], flags=flags)
    assert d.entries
