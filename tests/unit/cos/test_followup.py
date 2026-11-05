"""Follow-up research (TDD §14): bounded loop → answered / unanswerable, cited, claim-checked."""

import re
from datetime import timedelta

from app.agents.followup import run_followup
from app.contracts.agent_io import OpenQuestion
from tests.unit.cos.fakes import (
    FakeFetch,
    FakeModelClient,
    FakeSearch,
    all_text,
    claim_handler,
    extract_handler,
    tc,
)
from tests.unit.cos.fixtures import NOW, documents, user_ctx

Q = OpenQuestion(
    id="q1",
    question="What did the regulator say about SMR licensing?",
    created_at=NOW - timedelta(hours=10),
    context={"story_ref": "en-2"},
)
_FETCHED = re.compile(r'<untrusted_source id="([0-9a-f]{12}:\d+-\d+)"')


def researcher(final=None, *, cite_invented=False):
    """search → fetch the first hit → stop; then answer citing the first fetched passage."""

    def h(req):
        text = all_text(req)
        if req.response_schema is not None:
            if final is not None:
                return final
            pids = _FETCHED.findall(text)
            cites = ["invented:0-1"] if cite_invented else pids[:1]
            return {
                "status": "answered",
                "what_was_found": "Regulator guidance.",
                "answer_sentences": [
                    {"text": "The regulator issued guidance on SMR licensing.", "cites": cites}
                ],
            }
        n_tool = sum(1 for m in req.messages if m.role == "tool")
        if n_tool == 0:
            return [tc("search", query="SMR licensing regulator")]
        if n_tool == 1:
            m = re.search(r'<untrusted_source id="([0-9a-f]{12})"', text)
            if m is None:
                return "Nothing to read."
            doc_id = m.group(1)
            return [tc("fetch", doc_id=doc_id)]
        return "I have enough."

    return h


def tools(**kw):
    docs = documents()
    return {
        "search": kw.get("search", FakeSearch(docs, charge=False)),
        "fetch": kw.get("fetch", FakeFetch(docs)),
    }


def client(research=None, claims=None):
    return FakeModelClient(
        {
            "followup.research": research or researcher(),
            "cos.extract_passages": extract_handler(2),
            "cos.claim_check": claims or claim_handler(),
        }
    )


async def test_answered_with_verified_citations():
    c = client()
    ans, trace = await run_followup(Q, user_ctx=user_ctx(), model_client=c, tools=tools())
    assert ans.status == "answered" and ans.question_id == "q1"
    assert ans.answer_sentences[0].cites
    pids = {p.passage_id for p in ans.passages}
    assert set(ans.answer_sentences[0].cites) <= pids
    docs = {d.url_hash: d for d in documents()}
    for p in ans.passages:
        assert docs[p.doc_id].text[p.char_start : p.char_end] == p.text
    assert {"followup.research", "cos.extract_passages", "cos.claim_check"} <= set(c.roles())
    assert ans.researched_at == NOW


async def test_claim_check_failure_makes_it_unanswerable():
    ans, _ = await run_followup(
        Q, user_ctx=user_ctx(), tools=tools(), model_client=client(claims=claim_handler(unsupported={"a0"}))
    )
    assert ans.status == "unanswerable" and ans.answer_sentences == [] and ans.passages == []
    assert ans.what_was_found


async def test_model_says_unanswerable():
    final = {"status": "unanswerable", "what_was_found": "No official statement yet.", "answer_sentences": []}
    ans, _ = await run_followup(Q, user_ctx=user_ctx(), tools=tools(), model_client=client(researcher(final)))
    assert ans.status == "unanswerable" and ans.what_was_found == "No official statement yet."


async def test_invented_citations_are_removed():
    ans, _ = await run_followup(
        Q, user_ctx=user_ctx(), tools=tools(), model_client=client(researcher(cite_invented=True))
    )
    assert ans.status == "unanswerable"


async def test_search_and_fetch_caps():
    n = {"i": 0}

    def greedy(req):
        if req.response_schema is not None:
            return {"status": "unanswerable", "what_was_found": "x"}
        n["i"] += 1
        return [
            tc("search", n["i"], query="SMR licensing"),
            tc("fetch", n["i"], url="https://blog.example.org/smr-permit"),
        ]

    t = tools()
    ans, trace = await run_followup(Q, user_ctx=user_ctx(), tools=t, model_client=client(greedy))
    assert len(t["search"].calls) == 8 and len(t["fetch"].calls) == 6
    assert ans.status == "unanswerable"


async def test_budget_exhaustion_finalises_unanswerable():
    import copy

    from app.config import loader

    cfg = copy.deepcopy(loader.agent())
    cfg["budgets"]["followup"]["input_tokens"] = 50
    ans, trace = await run_followup(
        Q, user_ctx=user_ctx(), tools=tools(), model_client=client(), agent_cfg=cfg
    )
    assert ans.status == "unanswerable"
    assert trace.degradations


async def test_no_tools_available():
    ans, _ = await run_followup(
        Q,
        user_ctx=user_ctx(),
        tools={},
        model_client=client(researcher({"status": "unanswerable", "what_was_found": ""})),
    )
    assert ans.status == "unanswerable" and ans.what_was_found == "No relevant sources were found."


async def test_search_failure_reported_to_model_not_faked():
    seen = []

    def r(req):
        seen.append(all_text(req))
        return researcher({"status": "unanswerable", "what_was_found": "Search unavailable."})(req)

    t = tools(search=FakeSearch(documents(), charge=False, fail=True))
    ans, _ = await run_followup(Q, user_ctx=user_ctx(), tools=t, model_client=client(r))
    assert ans.status == "unanswerable"
    assert any("search failed" in s for s in seen)
