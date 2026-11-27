"""Smoke run: one user's daily run keeps a stored set, then one live voice turn about a stored story
that the brief did not cover.

    .venv/bin/python scripts/smoke_stored_set.py \
        --digests docs/smoke/2026-11-27/editor_to_brief --out docs/smoke/2026-11-27/stored_set

Inputs: the committed real digests (`digest_<beat>.json`) and the synthetic test user in
tests/unit/cos/fixtures.py. In process, no Postgres or worker: the job handler and the WebSocket only
wrap these same calls. The voice turn uses an in-memory NoteStore; pass --notes <json list of
StoryNote> to load real notes, otherwise it is empty and `get_story` takes the no-note fallback.

Model calls go through the Router (OPENROUTER_API_KEY); the ledger is written to <out>/llm_ledger.jsonl.
Live tools: `search` (SEARCH_PROVIDER / SEARCH_API_KEY) and `fetch`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from app.agents.cos import run_cos_daily
from app.agents.newsroom.note_store import InMemoryNoteStore
from app.agents.voice.session import VoiceConfig, VoiceSession
from app.agents.voice.store import MemorySessionStore
from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.digest import Digest
from app.contracts.notes import StoryNote
from app.contracts.session import ClientHello, Control, DebugSubscribe, Playback, Utterance
from app.contracts.tools import ToolContext
from app.llm.factory import get_client
from app.tools import build_tools
from tests.unit.cos.fixtures import memory, user_ctx

BEATS = ("ai", "energy", "physical_ai")


def _cost(ledger: Path, roles: set[str] | None = None) -> dict[str, float]:
    by_role: dict[str, float] = {}
    if not ledger.exists():
        return by_role
    for line in ledger.read_text().splitlines():
        if not line:
            continue
        r = json.loads(line)
        if roles is None or r["role"] in roles:
            by_role[r["role"]] = round(by_role.get(r["role"], 0) + float(r.get("cost_usd") or 0), 5)
    return by_role


async def voice_turn(
    client: Any, tools: dict, script, packs, story_set, notes: list[StoryNote], utterance: str, run_id: str
) -> dict[str, Any]:
    out: list[Any] = []
    store = MemorySessionStore("smoke-sess")

    async def send(m: Any) -> None:
        out.append(m)

    def tool_ctx(budget: TokenBudget) -> ToolContext:
        return ToolContext(
            mode="live",
            now=story_set.created_at,
            run_id=run_id,
            budget=budget,
            trace=TraceCtx(run_id=run_id, role="voice.quick_search"),
        )

    sess = VoiceSession(
        session_id="smoke-sess",
        user_id=None,
        brief=script,
        packs=packs,
        model=client,
        tools=tools,
        store=store,
        send=send,
        tool_ctx=tool_ctx,
        config=VoiceConfig(auto_resume_delay_s=0.0, idle_end_s=0),
        run_id=run_id,
        story_set=story_set,
        note_store=InMemoryNoteStore(notes),
    )
    await sess.handle(ClientHello(session_id="smoke-sess", client={"mode": "text"}))
    await sess.handle(DebugSubscribe(enabled=True))
    await sess.handle(Control(action="play"))
    await sess.handle(Playback(segment_id=script.sentences[-1].sid, offset_ms=0, completed=True))
    t0 = time.monotonic()
    await sess.handle(Utterance(text=utterance))
    if sess._turn_task:
        await sess._turn_task
    answer = [
        m for m in out if m.type == "transcript" and m.role == "agent" and not m.turn_id.startswith("s")
    ]
    return {
        "utterance": utterance,
        "answer": [{"text": m.text, "citations": m.citations,
                    "sources": [s.model_dump() for s in (m.sources or [])]} for m in answer],
        "debug": [m.text for m in out if m.type == "debug" and m.kind in ("tool", "model", "speak", "drop")],
        "s": round(time.monotonic() - t0, 1),
        "signals": store.signals,
    }


async def main(digest_dir: Path, out: Path, notes_path: Path | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "llm_ledger.jsonl"
    ledger.unlink(missing_ok=True)
    client = get_client(ledger_path=str(ledger))
    if client is None:
        raise SystemExit("OPENROUTER_API_KEY not set")
    digests = [
        Digest.model_validate_json((digest_dir / f"digest_{b}.json").read_text())
        for b in BEATS
        if (digest_dir / f"digest_{b}.json").exists()
    ]
    at = max(d.at for d in digests)
    run_id = f"smoke-stored-{at:%Y%m%d}"
    ctx = user_ctx(
        local_date=at.date(), now=at, mode="live", run_id=run_id, followed_beats=[d.beat for d in digests]
    )
    tools = build_tools(ledger=None)

    t0 = time.monotonic()
    script, packs, trace = await run_cos_daily(ctx, digests, memory(), [], [], None, client, tools)
    cos_s = round(time.monotonic() - t0, 1)
    ss = trace.story_set
    assert ss is not None
    (out / "stored_set.json").write_text(ss.model_dump_json(indent=1))
    (out / "brief.json").write_text(script.model_dump_json(indent=1))
    (out / "brief_script.txt").write_text("\n".join(s.text for s in script.sentences) + "\n")
    (out / "source_packs.json").write_text(json.dumps([p.model_dump(mode="json") for p in packs], indent=1))
    cos_cost = _cost(ledger)

    notes = (
        [StoryNote.model_validate(n) for n in json.loads(notes_path.read_text())] if notes_path else []
    )
    # A stored story the brief didn't cover and that has no source pack in this run (C8 can cut a
    # selected story whose pack then still sits in the CORPUS): a digest story, so the note store is
    # asked first.
    packed = {p.story_ref for p in packs}
    rest = [s for s in ss.stories if not s.in_brief]
    target = next(
        (s for s in rest if s.note_status == "shared" and s.story_ref not in packed),
        next((s for s in rest if s.story_ref not in packed), rest[0] if rest else None),
    )
    turn = None
    if target is not None:
        turn = await voice_turn(
            client, tools, script, packs, ss, notes,
            f"Before we go, what's the story with {target.headline}?", run_id,
        )
        turn["target"] = target.model_dump(mode="json")
        (out / "voice_turn.json").write_text(json.dumps(turn, indent=1, default=str))

    voice_cost = {k: v for k, v in _cost(ledger).items() if k.startswith("voice")}
    summary = {
        "digests": {d.beat: len(d.entries) for d in digests},
        "at": at.isoformat(),
        "cos": {"s": cos_s, "brief_stories": len(script.stories), "packs": len(packs),
                "stored_set": len(ss.stories),
                "per_user": sum(1 for s in ss.stories if s.note_status == "per_user")},
        "notes_loaded": len(notes),
        "voice_target": target.story_ref if target else None,
        "cost_usd": {
            "cos": round(sum(cos_cost.values()), 4),
            "voice": round(sum(voice_cost.values()), 4),
            "by_role": _cost(ledger),
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--digests", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--notes", type=Path)
    a = ap.parse_args()
    asyncio.run(main(a.digests, a.out, a.notes))
