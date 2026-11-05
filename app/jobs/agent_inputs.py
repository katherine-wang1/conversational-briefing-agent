"""Build the agents' typed inputs (app/contracts/agent_io.py) from database rows.

The job handlers own DB access; these helpers turn rows into `ShownStory` history, `SessionRecord`s,
`OpenQuestion`s and newsroom `Story` history so the real agents get exactly the shapes they expect.
All functions take an open user (or system) `Scope`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.contracts.agent_io import (
    FollowupAnswer,
    OpenQuestion,
    SessionOutcome,
    SessionRecord,
    ShownStory,
    TranscriptTurn,
)
from app.contracts.digest import Story, StoryFeatures
from app.contracts.memory import MemorySignal, SessionContext
from app.db.memory_io import row_to_signal

HISTORY_DAYS = 14
ENGAGED_SIGNALS = {"dive", "followup", "save_for_later", "bookmark", "more_like", "keep_posted"}
SAVED_SIGNALS = {"save_for_later", "bookmark"}
PRIOR_SESSIONS = 10


def _pack_urls(pack: dict[str, Any] | None) -> list[str]:
    if not pack:
        return []
    return list(dict.fromkeys(p.get("url") for p in pack.get("passages", []) if p.get("url")))


def shown_from_script(
    script: dict[str, Any],
    shown_on: date,
    signals: Iterable[MemorySignal],
    packs: dict[str, dict[str, Any]] | None = None,
    *,
    had_session: bool = True,
) -> list[ShownStory]:
    """The stories of one brief as `ShownStory`, with heard / engaged from that brief's sessions."""
    by_ref: dict[str, set[str]] = {}
    for sig in signals:
        if sig.story_ref:
            by_ref.setdefault(sig.story_ref, set()).add(sig.type)
    out = []
    for st in script.get("stories", []):
        ref = st["story_ref"]
        types = by_ref.get(ref, set())
        out.append(
            ShownStory(
                story_ref=ref,
                headline=st.get("headline", ""),
                shown_on=shown_on,
                slot=st.get("slot"),
                urls=_pack_urls((packs or {}).get(ref)),
                entities=list(st.get("aliases") or []),
                heard=had_session and "not_reached" not in types,
                engaged=bool(types & ENGAGED_SIGNALS),
                saved_for_later=bool(types & SAVED_SIGNALS),
            )
        )
    return out


async def shown_history(s: Any, local_date: date, days: int = HISTORY_DAYS) -> list[ShownStory]:
    """Stories from the user's briefs in the `days` before `local_date` (TDD §8: 14-day history)."""
    since = local_date - timedelta(days=days)
    briefs = [
        b
        for b in await s.briefs.list(order_by="local_date", desc=True, limit=days * 3)
        if since <= b["local_date"] < local_date and b.get("script")
    ]
    out: list[ShownStory] = []
    for b in briefs:
        sessions = await s.sessions.list(brief_id=b["id"])
        signals = []
        for sess in sessions:
            signals += [row_to_signal(r) for r in await s.memory_signals.list(session_id=sess["id"])]
        packs = {p["story_id"]: p["pack"] for p in await s.source_packs.list(brief_id=b["id"])}
        out += shown_from_script(b["script"], b["local_date"], signals, packs, had_session=bool(sessions))
    return out


def open_question_from_row(r: dict[str, Any]) -> OpenQuestion:
    known = ("open", "researching", "answered", "unanswerable", "dismissed")
    status = r["status"] if r["status"] in known else "answered"  # 'delivered' was answered
    answer = None
    if r.get("answer") and r["status"] in ("answered", "unanswerable"):
        a = dict(r["answer"])
        a.setdefault("question_id", r["id"])
        a.setdefault("status", r["status"])
        try:
            answer = FollowupAnswer.model_validate(
                {k: v for k, v in a.items() if k in FollowupAnswer.model_fields}
            )
        except ValueError:
            answer = None
    ctx = dict(r.get("context") or {})
    if r.get("story_ref"):
        ctx.setdefault("story_ref", r["story_ref"])
    return OpenQuestion(
        id=r["id"],
        question=r["question"],
        context=ctx,
        status=status,
        created_at=r["created_at"],
        answer=answer,
    )


async def prior_outcomes(s: Any, exclude_session: str, limit: int = PRIOR_SESSIONS) -> list[SessionOutcome]:
    out = []
    for r in await s.sessions.list(order_by="started_at", desc=True, limit=limit + 1):
        if r["id"] == exclude_session or not r.get("memory_outcome"):
            continue
        raw = (r["memory_outcome"] or {}).get("outcome")
        if raw:
            out.append(SessionOutcome.model_validate(raw))
    return out[:limit]


async def session_record(s: Any, session_id: str) -> tuple[SessionRecord, dict[str, Any] | None] | None:
    """(SessionRecord, brief row) for a finished session, or None if the session is gone."""
    sess = await s.sessions.get(session_id)
    if sess is None:
        return None
    turns = await s.turns.list(session_id=session_id)
    signals = [row_to_signal(r) for r in await s.memory_signals.list(session_id=session_id)]
    brief = await s.briefs.get(sess["brief_id"]) if sess["brief_id"] else None
    script = (brief or {}).get("script") or {}
    packs = (
        {p["story_id"]: p["pack"] for p in await s.source_packs.list(brief_id=brief["id"])} if brief else {}
    )
    shown = shown_from_script(script, brief["local_date"], signals, packs) if brief else []
    transcript = [
        TranscriptTurn(turn_id=t["id"], role=t["role"], text=t["text"] or "", story_ref=t["story_ref"])
        for t in turns
        if t["role"] in ("user", "agent")
    ]
    ctx = SessionContext(
        time_available_s=sess["time_available_s"],
        end_reason=sess["end_reason"],
        brief_length_s=script.get("est_duration_s"),
    )
    rec = SessionRecord(
        session_id=session_id,
        started_at=sess["started_at"],
        ctx=ctx,
        transcript=transcript,
        signals=signals,
        shown=shown,
    )
    return rec, brief


def story_from_row(r: dict[str, Any]) -> Story:
    return Story(
        story_id=r["id"],
        beat=r["beat"],
        title=r["title"],
        first_seen=r["first_seen"],
        item_urls=list(r.get("item_urls") or []),
        features=StoryFeatures.model_validate(r.get("features") or {}),
        update_of=r.get("update_of"),
    )


async def newsroom_history(s: Any, beat: str, at: datetime, days: int = 7) -> list[Story]:
    rows = await s.fetch_all(
        "select * from stories where run_id = %s and beat = %s and first_seen > %s and first_seen <= %s",
        [s.run_id, beat, at - timedelta(days=days), at],
    )
    return [story_from_row(r) for r in rows]


async def save_stories(s: Any, stories: Iterable[Story]) -> None:
    for st in stories:
        await s.stories.upsert(
            id=st.story_id,
            beat=st.beat,
            title=st.title,
            first_seen=st.first_seen.astimezone(UTC),
            item_urls=st.item_urls,
            features=st.features,
            update_of=st.update_of,
        )
