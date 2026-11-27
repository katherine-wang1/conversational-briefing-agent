"""Smoke run: shared story notes for committed digests, live fetch, model through the Router.

    .venv/bin/python scripts/smoke_story_notes.py \
        --digests docs/smoke/2026-11-27/editor_to_brief --per-beat 8 \
        --out docs/smoke/2026-11-27/story_notes

Runs `write_story_notes` (the same code the newsroom job runs) on the first `--per-beat` entries of each
`digest_<beat>.json`, with the live `fetch` tool (in-memory document cache) and OPENROUTER_API_KEY.
Writes notes.json (StoryNote list), outcomes.json (per story: seconds, tokens, facts dropped),
summary.json (per beat and overall, cost from the ledger), notes.md (every note, readable) and
llm_ledger.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.agents.cos.packs import url_host
from app.agents.newsroom.notes import AGGREGATOR_HOSTS, StoryNotesSettings, write_story_notes
from app.contracts.digest import Digest
from app.contracts.notes import StoryNote
from app.llm.factory import get_client
from app.tools import build_tools


def render_note(n: StoryNote, n_urls: int, seconds: float) -> str:
    out = [f"### {n.headline}", ""]
    out.append(
        f"`{n.story_id}` · {n.beat} · status **{n.status}** · {len(n.facts)} facts · "
        f"{n_urls} digest URLs · {seconds:.1f} s · {n.model or '-'}"
    )
    out.append("")
    ids = {}
    ok = [s for s in n.sources if s.status == "ok"]
    for i, s in enumerate(ok, start=1):
        ids[s.url] = f"S{i}"
    if n.facts:
        out.append("**Facts**")
        out.append("")
        for f in n.facts:
            out.append(f"- {f.text} [{', '.join(ids.get(u, u) for u in f.source_urls)}]")
        out.append("")
    if n.entities:
        out.append(f"**Entities:** {', '.join(n.entities)}")
        out.append("")
    if n.context:
        out.append(f"**Context:** {n.context}")
        out.append("")
    if n.why_it_matters:
        out.append(f"**Why it matters:** {n.why_it_matters}")
        out.append("")
    if n.open_questions:
        out.append("**Open questions:** " + " / ".join(n.open_questions))
        out.append("")
    out.append("**Sources tried**")
    out.append("")
    for s in n.sources:
        tag = f"{ids[s.url]} " if s.url in ids else ""
        extra = f" — {s.error}" if s.error else ""
        listed = f" (listed as {s.listed_url[:60]}…)" if s.listed_url else ""
        out.append(f"- {tag}{s.status}: {s.url[:110]}{listed}{extra}")
    out.append("")
    return "\n".join(out)


async def main(digests_dir: Path, beats: list[str], per_beat: int, out: Path, cap: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "llm_ledger.jsonl"
    ledger.unlink(missing_ok=True)
    client = get_client(ledger_path=str(ledger), spend_cap_usd=cap)
    if client is None:
        raise SystemExit("OPENROUTER_API_KEY not set")
    tools = build_tools(search_provider="", search_api_key="")
    st = StoryNotesSettings.from_config()
    notes, outcomes, per_beat_summary, md = [], [], {}, []
    t_all = time.monotonic()
    for beat in beats:
        d = Digest.model_validate_json((digests_dir / f"digest_{beat}.json").read_text())
        n_rows = len(ledger.read_text().splitlines()) if ledger.exists() else 0
        run = await write_story_notes(
            d,
            client,
            tools,
            settings=st,
            limit=per_beat,
            run_id=f"smoke-notes-{beat}",
            cache_namespace=f"smoke-notes-{time.time():.0f}",
        )
        rows = (
            [json.loads(x) for x in ledger.read_text().splitlines()[n_rows:] if x] if ledger.exists() else []
        )
        s = run.summary()
        s["cost_usd"] = round(sum(float(r.get("cost_usd") or 0) for r in rows), 4)
        s["llm_calls"] = len(rows)
        per_beat_summary[beat] = s
        urls_by_story = {e.story_id: e.urls for e in d.entries}
        md.append(f"## {beat} ({d.digest_id})\n")
        for o in run.outcomes:
            all_urls = urls_by_story[o.story_id]
            outcomes.append(
                {
                    "beat": beat,
                    "story_id": o.story_id,
                    "status": o.note.status if o.note else None,
                    "error": o.error,
                    "seconds": o.seconds,
                    "facts": len(o.note.facts) if o.note else 0,
                    "facts_proposed": o.facts_proposed,
                    "facts_dropped": o.facts_dropped,
                    "skipped_redirects": o.skipped_redirects,
                    "tokens_in": o.tokens_in,
                    "tokens_out": o.tokens_out,
                    "digest_urls": len(all_urls),
                    "digest_urls_google_news": sum(url_host(u) in AGGREGATOR_HOSTS for u in all_urls),
                }
            )
            if o.note:
                notes.append(o.note)
                md.append(render_note(o.note, len(all_urls), o.seconds))
            else:
                md.append(f"### (no note) {o.story_id}\n\n{o.error}\n")
    summary = {
        "digests": str(digests_dir),
        "per_beat": per_beat,
        "settings": st.model_dump(),
        "wall_s": round(time.monotonic() - t_all, 1),
        "beats": per_beat_summary,
        "cost_usd": round(sum(b["cost_usd"] for b in per_beat_summary.values()), 4),
    }
    (out / "notes.json").write_text(json.dumps([n.model_dump(mode="json") for n in notes], indent=1))
    (out / "outcomes.json").write_text(json.dumps(outcomes, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "notes.md").write_text(
        "# Story notes, live smoke run (2026-11-27)\n\nEvery note from `notes.json`, rendered. "
        "[S1] etc. are the note's sources fetched ok, in the order listed under *Sources tried*.\n\n"
        + "\n".join(md)
    )
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--digests", required=True)
    ap.add_argument("--beat", action="append")
    ap.add_argument("--per-beat", type=int, default=8)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spend-cap", type=float, default=0.8)
    a = ap.parse_args()
    asyncio.run(
        main(Path(a.digests), a.beat or ["ai", "energy", "physical_ai"], a.per_beat, Path(a.out), a.spend_cap)
    )
