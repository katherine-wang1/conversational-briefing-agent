"""Live smoke run of the editor newsroom for one beat: collect live sources, run, save digest + trace.

    .venv/bin/python scripts/smoke_editor_newsroom.py --beat ai --out docs/smoke/2026-11-26/editor

Needs OPENROUTER_API_KEY. Sources are fetched concurrently with the project's honest User-Agent;
a source that fails is skipped and listed. The LLM ledger goes to <out>/llm_ledger.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.agents.newsroom.editor import run_editor_newsroom
from app.config import loader
from app.config.env_file import load_env_file
from app.llm.router import Router
from app.sources.adapters import observe


async def collect(beat_id: str, at: datetime) -> tuple[list, dict]:
    beat = loader.beats()[beat_id]
    sem = asyncio.Semaphore(12)

    async def one(src):
        async with sem:
            try:
                return src.id, await asyncio.to_thread(observe, src, beat_id, at), None
            except Exception as e:  # noqa: BLE001 - report and skip
                return src.id, [], f"{type(e).__name__}: {str(e)[:120]}"

    results = await asyncio.gather(*(one(s) for s in beat.headline_sources))
    obs = [o for _, os_, _ in results for o in os_]
    per_source = {sid: {"n": len(os_), "error": err} for sid, os_, err in results}
    return obs, per_source


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beat", default="ai")
    ap.add_argument("--out", default="docs/smoke/editor")
    ap.add_argument("--spend-cap", type=float, default=0.50)
    args = ap.parse_args()
    load_env_file()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    at = datetime.now(UTC).replace(microsecond=0)

    t0 = time.monotonic()
    obs, per_source = await collect(args.beat, at)
    t_collect = time.monotonic() - t0

    router = Router.from_env(ledger_path=out / "llm_ledger.jsonl", spend_cap_usd=args.spend_cap)
    t1 = time.monotonic()
    digest, trace = await run_editor_newsroom(args.beat, at, obs, router, {}, None, run_id="smoke-editor")
    t_run = time.monotonic() - t1

    (out / f"digest_{digest.digest_id}.json").write_text(digest.model_dump_json(indent=2))
    (out / f"newsroom_trace_{digest.digest_id}.json").write_text(trace.model_dump_json(indent=2))
    summary = {
        "beat": args.beat,
        "at": at.isoformat(),
        "collect_s": round(t_collect, 1),
        "newsroom_s": round(t_run, 1),
        "observations": len(obs),
        "sources": per_source,
        "steps": {s.step: s.detail for s in trace.steps},
        "usage": trace.usage.model_dump(),
        "llm_calls": [
            {
                k: c.model_dump()[k]
                for k in ("model", "tokens_in", "tokens_out", "cost_usd", "latency_ms", "ok")
            }
            for c in trace.llm_calls
        ],
        "degraded": trace.degraded,
        "entries": len(digest.entries),
        "top": [
            {
                "importance": e.importance,
                "rules": e.importance_rules,
                "headline": e.headline,
                "domains": e.features.n_independent_domains,
                "evidence": e.evidence,
            }
            for e in digest.entries[:15]
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("sources",)}, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
