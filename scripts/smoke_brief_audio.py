"""Smoke run for the spoken brief (C7 write + C8 claim check) on committed inputs.

    PYTHONPATH=. .venv/bin/python scripts/smoke_brief_audio.py --mode full --out <dir> --tag after
    PYTHONPATH=. .venv/bin/python scripts/smoke_brief_audio.py --mode packs --out <dir> --tag after

Inputs come from a committed smoke run (default `docs/smoke/2026-11-27/editor_to_brief`):

- `--mode full`: `run_cos_daily` live in process (C1–C8) from the committed digests
  (`digest_<beat>.json`), with live search and fetch. Search results vary between runs, so the stories
  can differ from one run to the next.
- `--mode packs`: the same `run_cos_daily`, but C1–C6 replay the committed run (its C5 selection and
  scores from `cos_trace.json`, its `source_packs.json`), so only C7 and C8 call the model. This
  isolates the writing and claim-check steps for a before/after comparison on identical stories.

The user is the synthetic test user in tests/unit/cos/fixtures.py. Model calls go through the Router
(OPENROUTER_API_KEY); the ledger is written to <out>/<tag>_ledger.jsonl and the cost is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest import mock

from app.agents.cos import pipeline, run_cos_daily
from app.agents.cos.pool import PoolItem
from app.agents.cos.search_loop import SearchState
from app.agents.cos.selection import SelectionResult
from app.contracts.brief import Candidate, CandidateScores, SearchPlan, SelectedStory, SourcePack
from app.contracts.digest import Digest
from app.llm.factory import get_client
from app.tools import build_tools
from tests.unit.cos.fixtures import memory, user_ctx

BEATS = ["ai", "energy", "physical_ai"]


def replay_patches(src: Path) -> list:
    """Patch C1–C6 so the run reuses the committed selection, scores and source packs."""
    trace = json.loads((src / "cos_trace.json").read_text())
    c5 = next(e["detail"] for e in trace["events"] if e["step"] == "C5")
    selected = [SelectedStory(**s) for s in c5["selected"]]
    scores = {s.story_ref: CandidateScores(**c5["scores"][s.story_ref]) for s in selected}
    packs = {p["story_ref"]: SourcePack(**p) for p in json.loads((src / "source_packs.json").read_text())}
    pool = [
        PoolItem(
            candidate=Candidate(
                story_ref=s.story_ref,
                origin="digest" if not s.story_ref.startswith("u:") else "personal",
                headline=packs[s.story_ref].headline,
            )
        )
        for s in selected
    ]

    async def plan(*a, **k):  # noqa: ANN002, ANN003, ANN202
        return SearchPlan(queries=[])

    async def loop(*a, **k):  # noqa: ANN002, ANN003, ANN202
        return [], SearchState(0)

    async def score(*a, **k):  # noqa: ANN002, ANN003, ANN202
        return scores

    async def pack(cc, item, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        return packs[item.ref]

    return [
        mock.patch.object(pipeline, "plan_searches", plan),
        mock.patch.object(pipeline, "run_search_loop", loop),
        mock.patch.object(pipeline, "build_pool", lambda *a, **k: (pool, [])),
        mock.patch.object(pipeline, "score_pool", score),
        mock.patch.object(pipeline, "select", lambda *a, **k: SelectionResult(selected, c5["slot_targets"])),
        mock.patch.object(pipeline, "build_pack", pack),
    ]


async def main(mode: str, src: Path, out: Path, tag: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / f"{tag}_ledger.jsonl"
    ledger.unlink(missing_ok=True)
    client = get_client(ledger_path=str(ledger))
    if client is None:
        raise SystemExit("OPENROUTER_API_KEY not set")
    digests = [Digest.model_validate_json((src / f"digest_{b}.json").read_text()) for b in BEATS]
    at = max(d.at for d in digests)
    ctx = user_ctx(
        local_date=at.date(), now=at, mode="live", run_id=f"brief-audio-{tag}", followed_beats=BEATS
    )
    patches = replay_patches(src) if mode == "packs" else []
    for p in patches:
        p.start()
    try:
        script, packs, trace = await run_cos_daily(
            ctx, digests, memory(), [], [], None, client, build_tools(ledger=None)
        )
    finally:
        for p in patches:
            p.stop()
    lines = [f"[{s.sid} {s.kind} {s.story_ref or '-'}] {s.text}" for s in script.sentences]
    (out / f"{tag}.txt").write_text("\n".join(lines) + "\n")
    (out / f"{tag}_brief.json").write_text(script.model_dump_json(indent=1))
    (out / f"{tag}_trace.json").write_text(trace.model_dump_json(indent=1))
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    cost = round(sum(float(r.get("cost_usd") or 0) for r in rows), 4)
    print(json.dumps({"tag": tag, "mode": mode, "calls": len(rows), "cost_usd": cost}, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "packs"], required=True)
    ap.add_argument("--src", default="docs/smoke/2026-11-27/editor_to_brief")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", required=True)
    a = ap.parse_args()
    asyncio.run(main(a.mode, Path(a.src), Path(a.out), a.tag))
