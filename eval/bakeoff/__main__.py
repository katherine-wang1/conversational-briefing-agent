"""Bake-off CLI.

    python -m eval.bakeoff --role cos.score --models anthropic/claude-haiku-5.5,openai/gpt-6-luna --live
    python -m eval.bakeoff --role all --models a,b,tinker:inkling-small --reps 3 --cap 0.50 --live
    python -m eval.bakeoff --role cos.score --models x,y --fake          # offline smoke test

Models default to OpenRouter; prefix `tinker:` for Tinker. `--live` spends money and is fenced by a
hard ledger spend cap (`--cap`, USD). Outputs go to eval/runs/bakeoff/<run_id>/ (results.jsonl with raw
outputs for later judging, ledger.jsonl, summary.json) and a markdown summary on stdout.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.llm.errors import SpendCapExceeded
from app.llm.fake import FakeModelClient
from app.llm.ledger import JsonlLedgerSink, Ledger
from app.llm.router import Router
from eval.bakeoff.fixtures import cases_for
from eval.bakeoff.harness import Candidate, markdown_table, new_run_id, run_case, write_outputs

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "runs" / "bakeoff"


def _fake_client(case_role: str) -> FakeModelClient:
    """Offline client that answers every fixture shape plausibly (for --fake smoke runs)."""
    from app.llm.fake import FakeReply

    fake = FakeModelClient(enforce_budget=False)

    def respond(req):
        if req.tools:
            return FakeReply(tool_calls=[(req.tools[0].name, {"query": "inference accelerator tape-out"})])
        if req.response_schema is not None:
            name = req.response_schema.__name__
            if name == "ScoreBatch":
                s = {"relevance": 0.5, "adjacency": "core", "novelty": 0.5, "importance": 50,
                     "dive_likelihood": 0.3}
                return FakeReply(structured={"scores": [dict(s, story_ref=f"c{i}") for i in (1, 2, 3)]})
            if name == "DigestScoreBatch":
                return FakeReply(structured={"scores": [
                    {"story_id": "s1", "importance": 80, "evidence": ["hn_points=640"]},
                    {"story_id": "s2", "importance": 10, "evidence": ["n_sources=1"]}]})
            if name == "SearchPlan":
                return FakeReply(structured={"queries": [
                    {"query": "AI chip tape-out", "kind": "personal", "rationale": "m1",
                     "memory_refs": ["m1"]}]})
        return FakeReply(text="First silicon is expected in Q2.", chunks=4)

    fake.responder = respond
    return fake


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.bakeoff")
    ap.add_argument("--role", required=True, help="logical role (e.g. cos.score) or 'all'")
    ap.add_argument("--models", required=True, help="comma-separated; prefix tinker: for Tinker")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--cap", type=float, default=0.50, help="hard spend cap (USD) for this run")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="call real providers (spends money)")
    mode.add_argument("--fake", action="store_true", help="offline FakeModelClient smoke run")
    a = ap.parse_args(argv)

    cases = cases_for(a.role)
    if not cases:
        ap.error(f"no fixtures for role {a.role!r}")
    run_id = new_run_id(a.role)
    out_dir = a.out / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger([JsonlLedgerSink(out_dir / "ledger.jsonl")], spend_cap_usd=a.cap)
    router = None if a.fake else Router.from_env(ledger=ledger)

    results = []
    cands = [Candidate.parse(m) for m in a.models.split(",") if m]
    try:
        for cand in cands:
            for case in cases:
                for rep in range(a.reps):
                    if a.fake:
                        client = _fake_client(case.role)
                        client.ledger = ledger
                    else:
                        assert router is not None
                        client = router.bind(cand.provider, cand.model)
                    results.append(await run_case(client, cand, case, rep, run_id, ledger.n_calls,
                                                  lambda: ledger.n_calls))
                    print(f"{cand.label:45s} {case.id:32s} rep={rep} ok={results[-1].ok} "
                          f"spent=${ledger.total_cost_usd:.5f}", flush=True)
    except SpendCapExceeded as e:
        print(f"STOPPED: {e}")
    rows = write_outputs(out_dir, results, {"run_id": run_id, "role": a.role, "models": a.models,
                                            "reps": a.reps, "cap": a.cap, "live": a.live,
                                            "total_cost_usd": ledger.total_cost_usd})
    print(markdown_table(rows))
    print(f"outputs: {out_dir}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
