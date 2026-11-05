import json

import httpx

from app.llm.ledger import Ledger, MemoryLedgerSink
from app.llm.router import Router
from eval.bakeoff.__main__ import amain
from eval.bakeoff.fixtures import CASES, cases_for
from eval.bakeoff.harness import Candidate, run_case, summarize
from tests.unit.llm.helpers import Recorder, completion, make_openrouter


def test_candidate_parse():
    assert Candidate.parse("anthropic/claude-haiku-5.5").label == "openrouter:anthropic/claude-haiku-5.5"
    assert Candidate.parse("tinker:inkling-small") == Candidate("tinker", "inkling-small")


def test_fixtures_cover_core_roles():
    roles = {c.role for c in CASES}
    assert {"cos.score", "cos.search_loop", "voice.turn", "newsroom.score", "cos.plan"} <= roles
    assert len(cases_for("all")) == len(CASES)


async def test_fake_run_end_to_end(tmp_path, capsys):
    rc = await amain(["--role", "all", "--models", "m/a,tinker:b", "--fake", "--out", str(tmp_path)])
    assert rc == 0
    (run_dir,) = list(tmp_path.iterdir())
    rows = json.loads((run_dir / "summary.json").read_text())["summary"]
    assert {r["candidate"] for r in rows} == {"openrouter:m/a", "tinker:b"}
    assert all(r["schema_valid"] == 1.0 and r["tool_call_ok"] == 1.0 and r["errors"] == 0 for r in rows)
    lines = (run_dir / "results.jsonl").read_text().splitlines()
    assert len(lines) == 2 * len(CASES)
    assert "| candidate |" in capsys.readouterr().out


async def test_live_path_with_mocked_http_tracks_repair_and_tools():
    (score_case,) = [c for c in CASES if c.id == "cos.score/basic-3"]
    (tool_case,) = [c for c in CASES if c.id == "cos.search_loop/first-step"]
    good = {"scores": [{"story_ref": f"c{i}", "relevance": 0.1, "adjacency": "core", "novelty": 0.2,
                        "importance": 5, "dive_likelihood": 0.1} for i in (1, 2, 3)]}
    from tests.unit.llm.helpers import sse

    def stream_of(text):
        return httpx.Response(200, content=sse([
            {"model": "acme/json-model", "choices": [{"index": 0, "delta": {"content": text}}]},
            {"model": "acme/json-model", "choices": [],
             "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0001}}]))

    rec = Recorder([
        stream_of('{"scores": []}'),  # invalid (min_length=1) -> repair (non-stream)
        httpx.Response(200, json=completion(json.dumps(good))),
        httpx.Response(200, content=sse([
            {"model": "acme/json-model", "choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "c1",
                 "function": {"name": "search", "arguments": '{"query": "x"}'}}]}}]}])),
    ])
    ledger = Ledger([MemoryLedgerSink()])
    router = Router({"openrouter": make_openrouter(rec)}, ledger=ledger)
    cand = Candidate("openrouter", "acme/json-model")
    client = router.bind(cand.provider, cand.model)
    r1 = await run_case(client, cand, score_case, 0, "t", ledger.n_calls, lambda: ledger.n_calls)
    assert r1.schema_valid and r1.repaired and r1.ttft_ms is not None
    r2 = await run_case(client, cand, tool_case, 0, "t", ledger.n_calls, lambda: ledger.n_calls)
    assert r2.tool_call_ok and not r2.repaired
    (row,) = summarize([r1, r2])
    assert row["schema_valid"] == 1.0 and row["schema_valid_first_try"] == 0.0 and row["tool_call_ok"] == 1.0
