"""Full end-to-end run with a readable log of what happens behind the scenes.

    env -u CLAUDE_API_KEY -u ANTHROPIC_API_KEY PYTHONPATH=. .venv/bin/python scripts/e2e_run.py \
        --out docs/e2e/2026-11-27

Stages, in process (Postgres and the worker only wrap these same calls):
1. Collect every headline source for the three beats, live (free).
2. Newsroom per beat with the configured implementation (`newsroom.impl`; editor, Sonnet 5.5, prompt v4).
3. Story notes for every shared digest story (live fetch, Google News links resolved).
4. One user's daily run (the synthetic test user in tests/unit/cos/fixtures.py): plan, search, pool,
   score, select, source packs, write, claim check, and the stored set of 20.
5. Three voice turns in text mode: a brief story, a stored story outside the brief, and "what else?".

Writes `<out>/run_log.md` (the narrative) plus every intermediate artifact as JSON. Model calls go
through OpenRouter (`OPENROUTER_API_KEY`); the ledger is `<out>/llm_ledger.jsonl`. Search: SEARCH_PROVIDER.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.cos import run_cos_daily
from app.agents.newsroom import newsroom_impl, run_newsroom
from app.agents.newsroom.note_store import InMemoryNoteStore
from app.agents.newsroom.notes import write_story_notes
from app.config import loader
from app.llm.factory import get_client
from app.sources.google_news import is_google_news_url
from app.tools import build_tools
from eval.newsroom_compare.arms import collect, load_observations
from scripts.smoke_stored_set import voice_turn
from tests.unit.cos.fixtures import memory, user_ctx

BEATS = ("ai", "energy", "physical_ai")


def ledger_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x] if path.exists() else []


def cost_since(path: Path, start: int) -> tuple[float, dict[str, float]]:
    rows = ledger_rows(path)[start:]
    by: dict[str, float] = {}
    for r in rows:
        by[r["role"]] = by.get(r["role"], 0.0) + float(r.get("cost_usd") or 0)
    return round(sum(by.values()), 4), {k: round(v, 4) for k, v in by.items()}


def esc(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def model_of(role: str) -> str:
    mr = loader.models().for_role(role)
    return f"{mr.model} ({mr.provider})"


class Log:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def add(self, *lines: str) -> None:
        self.parts.extend(lines)

    def table(self, header: list[str], rows: list[list[Any]]) -> None:
        self.add("| " + " | ".join(header) + " |", "|" + "---|" * len(header))
        self.add(*["| " + " | ".join(esc(c) for c in r) + " |" for r in rows])
        self.add("")

    def text(self) -> str:
        return "\n".join(self.parts) + "\n"


async def main(out: Path, day: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("NEWSROOM_COMPARE_DIR", str(out / "collection"))
    ledger = out / "llm_ledger.jsonl"
    ledger.unlink(missing_ok=True)
    client = get_client(ledger_path=str(ledger))
    if client is None:
        raise SystemExit("OPENROUTER_API_KEY not set")
    tools = build_tools(ledger=None)
    log = Log()
    started = datetime.now(UTC)
    timings: dict[str, float] = {}
    costs: dict[str, float] = {}

    log.add(
        f"# End-to-end run, {started:%Y-%m-%d %H:%M} UTC",
        "",
        "What one daily cycle does, stage by stage, from collecting headlines to a live question about a "
        "story the brief didn't cover. Everything below is real output from this run; nothing is edited. "
        "Artifacts (JSON) are next to this file.",
        "",
        "## Setup",
        "",
    )
    log.table(
        ["Setting", "Value"],
        [
            [
                "Newsroom",
                f"`{newsroom_impl(None)}`, model {model_of('newsroom.editor')}, prompt "
                f"`{(loader.agent().get('newsroom_editor') or {}).get('prompt', 'newsroom.editor')}`",
            ],
            ["Story notes", f"model {model_of('newsroom.story_note')}"],
            ["Per-user run (C1–C8) and voice", f"model {model_of('cos.write_brief')}"],
            ["Search provider", os.environ.get("SEARCH_PROVIDER") or "none"],
            ["Anthropic key", "not used (unset for this run)"],
            ["User", "synthetic test user (`tests/unit/cos/fixtures.py`), following all three beats"],
        ],
    )

    # ---- 1. collect ---------------------------------------------------------------------------------
    t0 = time.monotonic()
    at = datetime.now(UTC)
    rows = []
    for b in BEATS:
        meta = await collect(b, at, day)
        failed = [k for k, v in meta.items() if v.get("error")]
        rows.append([b, sum(v["n"] for v in meta.values()), len(meta), ", ".join(failed) or "none"])
    timings["1 collect"] = round(time.monotonic() - t0, 1)
    log.add(
        "## 1. Collect headlines",
        "",
        f"Every configured source for each beat is fetched at {at:%H:%M} UTC: RSS feeds, Google News topic "
        "searches, site front pages, Techmeme, Hacker News, arXiv. No model calls. "
        f"Took {timings['1 collect']} s.",
        "",
    )
    log.table(["Beat", "Headlines", "Sources", "Sources failed"], rows)

    # ---- 2. newsroom --------------------------------------------------------------------------------
    t0 = time.monotonic()
    start = len(ledger_rows(ledger))
    digests, nrows, tops = [], [], []
    for b in BEATS:
        tb = time.monotonic()
        digest, trace = await run_newsroom(
            b, at, load_observations(day, b), client, {}, None, run_id=f"e2e-{b}"
        )
        (out / f"digest_{b}.json").write_text(digest.model_dump_json(indent=1))
        (out / f"newsroom_trace_{b}.json").write_text(trace.model_dump_json(indent=1))
        digests.append(digest)
        st = {s.step: (s.detail or {}) for s in trace.steps}
        gn_only = sum(1 for e in digest.entries if e.urls and all(is_google_news_url(u) for u in e.urls))
        nrows.append(
            [
                b,
                st.get("N2", {}).get("observations_in"),
                st.get("N2", {}).get("observations_kept"),
                st.get("N3", {}).get("candidates"),
                st.get("N4", {}).get("shown"),
                st.get("N5", {}).get("stories"),
                len(digest.entries),
                gn_only,
                round(time.monotonic() - tb, 1),
                ", ".join(trace.degraded) or "none",
            ]
        )
        tops.append((b, digest.entries[:5]))
    timings["2 newsroom"] = round(time.monotonic() - t0, 1)
    costs["2 newsroom"], _ = cost_since(ledger, start)
    log.add(
        "## 2. Newsroom (shared, once for everyone)",
        "",
        "Per beat: drop items outside the 24 h window and stale ones (N2), merge duplicate headlines into "
        "candidates (N3), order candidates by source signals and cut to the strongest (N4), one editor "
        "call groups candidates into stories and scores importance (N5), then keep the top 25–30 as the "
        f"beat's digest (N6). Took {timings['2 newsroom']} s, ${costs['2 newsroom']}.",
        "",
    )
    log.table(
        [
            "Beat",
            "Headlines in",
            "Kept (window)",
            "Candidates",
            "Shown to editor",
            "Stories",
            "Digest",
            "Google-News-only links",
            "s",
            "Degraded",
        ],
        nrows,
    )
    for b, entries in tops:
        log.add(f"**{b} top 5:**", "")
        for i, e in enumerate(entries, 1):
            log.add(f"{i}. **{e.headline}** (importance {e.importance}; {len(e.urls)} links). {e.summary}")
        log.add("")

    # ---- 3. story notes -----------------------------------------------------------------------------
    t0 = time.monotonic()
    start = len(ledger_rows(ledger))
    store = InMemoryNoteStore()
    all_notes, srows, src_counter = [], [], Counter()
    example = None
    for d in digests:
        tb = time.monotonic()
        run = await write_story_notes(d, client, tools, mode="live", now=at, run_id="e2e")
        notes = [o.note for o in run.outcomes if o.note is not None]
        await store.put_many(notes)
        all_notes.extend(notes)
        status = Counter(n.status for n in notes)
        for n in notes:
            for s in n.sources:
                src_counter[s.status] += 1
        facts = [len(n.facts) for n in notes if n.facts]
        srows.append(
            [
                d.beat,
                len(d.entries),
                status.get("ok", 0),
                status.get("partial", 0),
                status.get("no_text", 0),
                len(d.entries) - len(notes),
                round(sum(facts) / len(facts), 1) if facts else 0,
                round(time.monotonic() - tb, 1),
            ]
        )
        if example is None:
            example = next((n for n in notes if n.status == "ok" and len(n.facts) >= 4), None)
    (out / "story_notes.json").write_text(
        json.dumps([n.model_dump(mode="json") for n in all_notes], indent=1)
    )
    timings["3 story notes"] = round(time.monotonic() - t0, 1)
    costs["3 story notes"], _ = cost_since(ledger, start)
    log.add(
        "## 3. Story notes (shared, once per story)",
        "",
        "For every digest story: pick up to 3 sources (primary source and free outlets first, paywalled "
        "last), fetch them through the `fetch` tool (Google News links are resolved to the publisher "
        "first), then one model call writes the note. Every fact must cite a source that was fetched and "
        f"quote it exactly, or code drops it. Took {timings['3 story notes']} s, ${costs['3 story notes']}.",
        "",
    )
    log.table(["Beat", "Stories", "ok", "partial", "no text", "no note", "Facts per note", "s"], srows)
    log.add(f"Sources tried across all notes: {dict(src_counter)}.", "")
    if example:
        log.add(f"**Example note: {example.headline}** ({example.beat}, status {example.status})", "")
        for f in example.facts[:8]:
            log.add(f"- {f.text} [{', '.join(f.source_urls)}]")
        log.add("", f"*Context:* {example.context}", "", f"*Why it matters:* {example.why_it_matters}", "")
        if example.open_questions:
            log.add("*Open questions:* " + "; ".join(example.open_questions), "")

    # ---- 4. per-user run ----------------------------------------------------------------------------
    t0 = time.monotonic()
    start = len(ledger_rows(ledger))
    mem = memory()
    ctx = user_ctx(local_date=at.date(), now=at, mode="live", run_id="e2e-user", followed_beats=list(BEATS))
    script, packs, trace = await run_cos_daily(ctx, digests, mem, [], [], None, client, tools)
    timings["4 per-user run"] = round(time.monotonic() - t0, 1)
    costs["4 per-user run"], cos_by_role = cost_since(ledger, start)
    ss = trace.story_set
    (out / "brief.json").write_text(script.model_dump_json(indent=1))
    (out / "brief_script.txt").write_text("\n".join(s.text for s in script.sentences) + "\n")
    (out / "source_packs.json").write_text(json.dumps([p.model_dump(mode="json") for p in packs], indent=1))
    (out / "cos_trace.json").write_text(trace.model_dump_json(indent=1))
    if ss:
        (out / "stored_set.json").write_text(ss.model_dump_json(indent=1))
    ev = [e.model_dump(mode="json") for e in trace.events]

    def code(step: str) -> list[dict]:
        return [e["detail"] for e in ev if e["step"] == step and e["kind"] == "code"]

    def tool_events(step: str, tool: str) -> list[dict]:
        return [
            e["detail"]
            for e in ev
            if e["step"] == step and e["kind"] == "tool" and e["detail"].get("tool") == tool
        ]

    def degrades(step: str) -> list[dict]:
        return [e["detail"] for e in ev if e["step"] == step and e["kind"] == "degrade"]

    log.add(
        "## 4. One user's daily run (per user)",
        "",
        f"User interests: {', '.join(f'{i.label} ({i.weight})' for i in mem.interests)}.",
        "",
        f"Took {timings['4 per-user run']} s, ${costs['4 per-user run']} "
        f"({', '.join(f'{k} ${v}' for k, v in cos_by_role.items())}).",
        "",
    )
    plan = next((d for d in code("C1") if "queries" in d), {})
    log.add("**C1 plan:** the model plans searches from the user's interests:", "")
    for q in plan.get("queries", []):
        log.add(f"- `{q['query']}` ({q['kind']}): {q.get('rationale', '')}")
    searches = tool_events("C2", "search")
    c2 = next((d for d in code("C2") if "finds" in d), {})
    log.add("", f"**C2 search:** {len(searches)} searches, {c2.get('finds')} finds kept.", "")
    for d in searches:
        log.add(f"- `{d.get('query')}` ({d.get('query_kind')}): {d.get('n_hits')} hits")
    log.add("")
    c3 = next(iter(code("C3")), {})
    log.add(
        f"**C3 pool:** {c3.get('pool_size')} candidates by origin {c3.get('origins')}; "
        f"notes: {c3.get('notes')}.",
        "",
    )
    c5 = next(iter(code("C5")), {})
    log.add("**C5 select** (slots " + json.dumps(c5.get("slot_targets")) + "):", "")
    log.table(
        ["Story", "Slot", "Why"],
        [[s.get("story_ref"), s.get("slot"), s.get("why_tag")] for s in c5.get("selected", [])],
    )
    fetches = tool_events("C6", "fetch")
    log.add(
        f"**C6 source packs:** {len(packs)} packs; {len(fetches)} fetches, "
        f"{sum(1 for d in fetches if d.get('error'))} failed: "
        + "; ".join(f"{d.get('url', '')[:60]} ({d['error']})" for d in fetches if d.get("error"))
        + ".",
        "",
    )
    for step in ("C6", "C8"):
        dg = degrades(step)
        if dg:
            reasons = Counter(d.get("reason", "?") for d in dg)
            log.add(f"*{step} degraded:* " + "; ".join(f"{r} ×{n}" for r, n in reasons.items()), "")
    c7 = [d for d in code("C7") if "notes" in d]
    c8 = [d for d in code("C8") if "checked" in d]
    log.add(
        f"**C7 write:** {c7[0].get('n_sentences') if c7 else '?'} sentences; notes: "
        f"{c7[0].get('notes') if c7 else []}.",
        "",
    )
    if c8:
        log.add(
            f"**C8 claim check:** {c8[0].get('checked')} checked, dropped {c8[0].get('dropped')}, "
            f"rewritten {c8[0].get('rewritten')}.",
            "",
        )
    log.add(f"### The spoken brief ({len(script.stories)} stories, {len(script.sentences)} sentences)", "")
    log.add(*[f"> {s.text}" for s in script.sentences], "")
    if ss:
        noted = {n.story_id for n in all_notes if n.status != "no_text"}
        log.add(f"### Stored set ({len(ss.stories)} stories kept for the conversation)", "")
        log.table(
            ["#", "Story", "In brief", "Note", "Relevance", "Importance"],
            [
                [
                    s.rank,
                    s.headline[:90],
                    "yes" if s.in_brief else "",
                    ("shared note" if s.story_ref in noted else s.note_status),
                    s.relevance,
                    s.importance,
                ]
                for s in ss.stories
            ],
        )

    # ---- 5. voice -----------------------------------------------------------------------------------
    t0 = time.monotonic()
    start = len(ledger_rows(ledger))
    turns = []
    if ss and script.stories:
        brief_story = script.stories[0]
        packed = {p.story_ref for p in packs}
        outside = next(
            (
                s
                for s in ss.stories
                if not s.in_brief
                and s.story_ref in {n.story_id for n in all_notes if n.status != "no_text"}
                and s.story_ref not in packed
            ),
            None,
        )
        questions = [f"Tell me more about {brief_story.headline}. Why does it matter?"]
        if outside:
            questions.append(f"What's the story with {outside.headline}?")
        questions.append("What else happened today that I should know about?")
        for q in questions:
            turns.append(await voice_turn(client, tools, script, packs, ss, all_notes, q, "e2e-voice"))
    (out / "voice_turns.json").write_text(json.dumps(turns, indent=1, default=str))
    timings["5 voice"] = round(time.monotonic() - t0, 1)
    costs["5 voice"], _ = cost_since(ledger, start)
    log.add(
        "## 5. Live conversation (text mode)",
        "",
        "Each question is a fresh session after the brief has played. The voice agent sees the brief, the "
        "source packs and the stored-set list in its prompt, and can call `get_story` (the shared note), "
        f"`quick_search` and others. Took {timings['5 voice']} s, ${costs['5 voice']}.",
        "",
    )
    for t in turns:
        log.add(f"**Q:** {t['utterance']}", "")
        tools_used = [
            x for x in t["debug"] if x.startswith(("tool", "get_story", "quick_search")) or "tool" in x
        ]
        if tools_used:
            log.add("*Behind the scenes:* " + " · ".join(x[:140] for x in tools_used[:6]), "")
        for a in t["answer"]:
            log.add(f"> {a['text']}")
        srcs = {s.get("url") for a in t["answer"] for s in a.get("sources", []) if s.get("url")}
        log.add("", f"*{t['s']} s; sources shown: {len(srcs)}*", "")

    # ---- totals -------------------------------------------------------------------------------------
    total, by_role = cost_since(ledger, 0)
    log.add("## Totals", "")
    log.table(
        ["Stage", "Time (s)", "Cost ($)"],
        [[k, v, costs.get(k, 0.0)] for k, v in timings.items()] + [["all", sum(timings.values()), total]],
    )
    log.add("Cost by model role:", "")
    log.table(["Role", "$"], [[k, v] for k, v in sorted(by_role.items(), key=lambda x: -x[1])])
    (out / "run_log.md").write_text(log.text())
    (out / "summary.json").write_text(
        json.dumps({"timings": timings, "costs": costs, "total": total, "by_role": by_role}, indent=1)
    )
    print(log.text()[-1500:])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--day", default=f"e2e-{datetime.now(UTC):%Y-%m-%d}")
    a = ap.parse_args()
    asyncio.run(main(a.out, a.day))
