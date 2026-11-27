"""Live check for paywalled / blocked stories: newsroom (so digests carry `source_snippets`), then story
notes for every digest story with the free-coverage search and publisher snippets.

    env -u CLAUDE_API_KEY -u ANTHROPIC_API_KEY \
        NEWSROOM_COMPARE_DIR=/path/to/eval/data/newsroom_compare PYTHONPATH=. \
        .venv/bin/python scripts/smoke_paywalls.py --day e2e-2026-11-27b --out docs/smoke/2026-11-27/paywalls

Reads the collection's observations (read only); `--digests DIR` reuses saved digests instead of running the
newsroom again. Model calls through OpenRouter (spend cap `--cap`); search
through SEARCH_PROVIDER. Writes digest_<beat>.json, notes.json, outcomes.json, summary.json and
llm_ledger.jsonl to `--out`. "Before" for each story is what the previous code would have written: the same
listed-URL fetches without free coverage or snippets (no ok listed source = no_text).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agents.newsroom import run_newsroom
from app.agents.newsroom.notes import StoryNotesSettings, write_story_notes
from app.contracts.digest import Digest
from app.contracts.notes import StoryNote
from app.llm.factory import get_client
from app.tools import build_tools
from app.tools.base import ToolError
from eval.newsroom_compare.arms import load_observations
from eval.newsroom_compare.common import day_dir

BEATS = ("ai", "energy", "physical_ai")


def count_provider_calls(tools: dict[str, Any], limit: int) -> Counter:
    """Wrap the live search provider so every provider call (the `search` tool and the Google News
    resolver's search fallback share it) is counted; past `limit` calls it refuses (a spend guard)."""
    counter: Counter = Counter()
    provider = getattr(tools["search"].live, "provider", None)
    if provider is None:
        return counter
    orig = provider.search

    async def counted(args: Any) -> Any:
        if counter["calls"] >= limit:
            counter["refused"] += 1
            raise ToolError("search_unavailable", f"smoke search cap {limit} reached")
        counter["calls"] += 1
        return await orig(args)

    provider.search = counted  # type: ignore[method-assign]
    return counter


def before_status(n: StoryNote, want: int) -> str:
    listed_ok = [s for s in n.sources if s.status == "ok" and s.found_via is None]
    if not listed_ok:
        return "no_text"
    return "ok" if len(listed_ok) >= want else "partial"


async def main(
    day: str, out: Path, cap: float, beats: list[str], max_searches: int, digests: Path | None
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "llm_ledger.jsonl"
    ledger.unlink(missing_ok=True)
    client = get_client(ledger_path=str(ledger), spend_cap_usd=cap)
    if client is None:
        raise SystemExit("OPENROUTER_API_KEY not set")
    tools = build_tools(ledger=None)
    searches = count_provider_calls(tools, max_searches)
    st = StoryNotesSettings.from_config()
    meta = json.loads((day_dir(day, beats[0]) / "collect_meta.json").read_text())
    at = datetime.fromisoformat(meta["at"])

    def cost() -> float:
        rows = [json.loads(x) for x in ledger.read_text().splitlines() if x] if ledger.exists() else []
        return round(sum(float(r.get("cost_usd") or 0) for r in rows), 4)

    summary: dict[str, Any] = {"day": day, "at": at.isoformat(), "beats": {}}
    notes_all: list[dict] = []
    outcomes: list[dict] = []
    t_all = time.monotonic()
    for beat in beats:
        t0, c0, s0 = time.monotonic(), cost(), searches["calls"]
        if digests is not None:  # reuse a previous run's digests (no newsroom call)
            digest = Digest.model_validate_json((digests / f"digest_{beat}.json").read_text())
        else:
            digest, _trace = await run_newsroom(
                beat, at, load_observations(day, beat), client, {}, None, run_id=f"paywalls-{beat}"
            )
            (out / f"digest_{beat}.json").write_text(digest.model_dump_json(indent=1))
        t1, c1, s1 = time.monotonic(), cost(), searches["calls"]
        run = await write_story_notes(
            digest, client, tools, settings=st, mode="live", now=at, run_id="paywalls"
        )
        t2, c2, s2 = time.monotonic(), cost(), searches["calls"]
        by_id = {e.story_id: e for e in digest.entries}
        before, after = Counter(), Counter()
        for o in run.outcomes:
            e = by_id[o.story_id]
            n = o.note
            want = min(st.max_sources_per_story, len(e.urls)) or 1
            b = before_status(n, want) if n else "no_note"
            a = n.status if n else "no_note"
            before[b] += 1
            after[a] += 1
            outcomes.append(
                {
                    "beat": beat,
                    "story_id": o.story_id,
                    "headline": e.headline,
                    "before": b,
                    "after": a,
                    "rescued_by": o.rescued_by,
                    "facts": len(n.facts) if n else 0,
                    "facts_dropped": o.facts_dropped,
                    "error": o.error,
                    "seconds": o.seconds,
                    "listed_urls": e.urls,
                    "source_snippets": [s.model_dump(mode="json") for s in e.source_snippets],
                    "free_coverage": o.free_coverage,
                    "sources": [s.model_dump(mode="json", exclude_none=True) for s in n.sources] if n else [],
                    "fact_texts": [
                        {"text": f.text, "sources": f.source_urls} for f in (n.facts if n else [])
                    ],
                }
            )
            if n:
                notes_all.append(n.model_dump(mode="json"))
        s = run.summary()
        summary["beats"][beat] = {
            "digest_entries": len(digest.entries),
            "entries_with_snippet_text": sum(
                any(x.snippet for x in e.source_snippets) for e in digest.entries
            ),
            "source_snippets": sum(len(e.source_snippets) for e in digest.entries),
            "before": dict(before),
            "after": dict(after),
            "rescued": s["rescued"],
            "free_coverage_searches": s["free_coverage_searches"],
            "sources": s["sources"],
            "newsroom_seconds": round(t1 - t0, 1),
            "newsroom_cost_usd": round(c1 - c0, 4),
            "notes_seconds": round(t2 - t1, 1),
            "notes_cost_usd": round(c2 - c1, 4),
            "search_provider_calls_newsroom": s1 - s0,
            "search_provider_calls_notes": s2 - s1,
        }
        print(beat, json.dumps(summary["beats"][beat]))
    summary["seconds"] = round(time.monotonic() - t_all, 1)
    summary["cost_usd"] = cost()
    summary["search_provider_calls"] = searches["calls"]
    summary["search_provider_refused"] = searches["refused"]
    (out / "notes.json").write_text(json.dumps(notes_all, indent=1))
    (out / "outcomes.json").write_text(json.dumps(outcomes, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "beats"}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="e2e-2026-11-27b")
    ap.add_argument("--out", type=Path, default=Path("docs/smoke/2026-11-27/paywalls"))
    ap.add_argument("--cap", type=float, default=1.5)
    ap.add_argument("--beats", nargs="*", default=list(BEATS))
    ap.add_argument("--max-searches", type=int, default=140)
    ap.add_argument("--digests", type=Path, default=None, help="reuse digest_<beat>.json from this folder")
    a = ap.parse_args()
    asyncio.run(main(a.day, a.out, a.cap, a.beats, a.max_searches, a.digests))
