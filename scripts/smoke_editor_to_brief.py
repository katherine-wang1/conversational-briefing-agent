"""Smoke run: the default newsroom on a collected day, then one user's brief from its digests.

    .venv/bin/python scripts/smoke_editor_to_brief.py --day 2026-11-26-late \
        --out docs/smoke/2026-11-27/editor_to_brief

Checks that the default newsroom's digests (`newsroom.impl`, config/agent.yaml) work as input to the
chief-of-staff run (`run_cos_daily`), in process, without Postgres or the worker: those only wrap these
same two calls. Observations come from `eval/data/newsroom_compare/<day>/` (`python -m
eval.newsroom_compare collect`). The user is the synthetic test user in tests/unit/cos/fixtures.py
(interests: AI agents and memory, SMR licensing), on the collection's date.

Live tools: `search` uses SEARCH_PROVIDER / SEARCH_API_KEY (or none: it then degrades), `fetch` is live.
Model calls go through the Router (OPENROUTER_API_KEY); the ledger is written to <out>/llm_ledger.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.agents.cos import run_cos_daily
from app.agents.newsroom import newsroom_impl, run_newsroom
from app.llm.factory import get_client
from app.tools import build_tools
from eval.newsroom_compare.arms import collected_at, load_observations
from tests.unit.cos.fixtures import memory, user_ctx


def dump(obj) -> str:  # noqa: ANN001
    return obj.model_dump_json(indent=1) if hasattr(obj, "model_dump_json") else json.dumps(obj, indent=1)


async def main(day: str, beats: list[str], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "llm_ledger.jsonl"
    ledger.unlink(missing_ok=True)
    client = get_client(ledger_path=str(ledger))
    if client is None:
        raise SystemExit("OPENROUTER_API_KEY not set")
    summary: dict = {"day": day, "newsroom_impl": newsroom_impl(None), "beats": {}}

    digests = []
    for beat in beats:
        at = collected_at(day, beat)
        t0 = time.monotonic()
        digest, trace = await run_newsroom(
            beat, at, load_observations(day, beat), client, {}, None, run_id=f"smoke-{day}-{beat}"
        )
        (out / f"digest_{beat}.json").write_text(dump(digest))
        (out / f"newsroom_trace_{beat}.json").write_text(dump(trace))
        summary["beats"][beat] = {
            "entries": len(digest.entries),
            "s": round(time.monotonic() - t0, 1),
            "degraded": getattr(trace, "degraded", []),
        }
        digests.append(digest)

    at = collected_at(day, beats[0])
    ctx = user_ctx(local_date=at.date(), now=at, mode="live", run_id=f"smoke-{day}", followed_beats=beats)
    tools = build_tools(ledger=None)
    t0 = time.monotonic()
    script, packs, trace = await run_cos_daily(ctx, digests, memory(), [], [], None, client, tools)
    summary["cos"] = {
        "s": round(time.monotonic() - t0, 1),
        "stories": len(script.stories),
        "sentences": len(script.sentences),
        "packs": len(packs),
    }
    (out / "brief.json").write_text(dump(script))
    (out / "brief_script.txt").write_text("\n".join(s.text for s in script.sentences) + "\n")
    (out / "source_packs.json").write_text(json.dumps([p.model_dump(mode="json") for p in packs], indent=1))
    (out / "cos_trace.json").write_text(dump(trace))

    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    by_role: dict[str, float] = {}
    for r in rows:
        by_role[r["role"]] = round(by_role.get(r["role"], 0) + float(r.get("cost_usd") or 0), 4)
    summary["cost_usd"] = {"total": round(sum(by_role.values()), 4), "by_role": by_role}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--beat", action="append")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    asyncio.run(main(a.day, a.beat or ["ai", "energy", "physical_ai"], Path(a.out)))
