"""WebSocket endpoint `/ws/session/{id}` (TDD §12.5).

Auth: browsers can't set headers on a WebSocket, so the Supabase token comes as `?token=` (or a
Bearer header for non-browser clients). The session row is loaded in the caller's user scope, so a
user can only ever attach to their own sessions. One active socket per user: a new connection
closes the previous one.

Close codes: 4401 unauthenticated, 4403 not invited, 4404 unknown session, 4409 replaced by a
newer connection, 4410 session already ended.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.agents.voice.session import VoiceConfig, VoiceSession
from app.agents.voice.store import DbSessionStore
from app.api.auth import AuthError, bearer, resolve_user
from app.config import loader
from app.contracts.brief import BriefScript, SourcePack
from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.session import ClientMessage, ErrorMsg
from app.contracts.tools import ToolContext
from app.jobs.deps import _now

log = logging.getLogger(__name__)
router = APIRouter()
_client_msg: TypeAdapter[Any] = TypeAdapter(ClientMessage)


async def load_session_context(db: Any, user_id: Any, session_id: str) -> dict[str, Any] | None:
    async with db.user_scope(user_id) as s:
        sess = await s.sessions.get(session_id)
        if sess is None:
            return None
        brief = await s.briefs.get(sess["brief_id"]) if sess["brief_id"] else None
        packs = await s.source_packs.list(brief_id=sess["brief_id"]) if brief else []
        prof = await s.profiles.me()
        questions = await s.open_questions.list(status=["open", "researching"], run_id=sess["run_id"])
    return {
        "session": sess,
        "brief": BriefScript.model_validate(brief["script"]) if brief and brief.get("script") else None,
        "packs": [SourcePack.model_validate(p["pack"]) for p in packs],
        "settings": (prof or {}).get("settings") or {},
        "open_questions": "; ".join(q["question"] for q in questions[:5]),
    }


@router.websocket("/ws/session/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str, token: str | None = Query(default=None)) -> None:
    st = websocket.app.state
    try:
        user = await resolve_user(st, token or bearer(websocket))
    except AuthError as e:
        await websocket.close(code=4403 if e.status == 403 else 4401, reason=e.code)
        return
    ctx = await load_session_context(st.db, user.user_id, session_id)
    if ctx is None:
        await websocket.close(code=4404, reason="unknown session")
        return
    if ctx["session"]["ended_at"] is not None:
        await websocket.close(code=4410, reason="session ended")
        return
    await websocket.accept()

    previous = st.active_sockets.get(user.user_id)
    if previous is not None:
        try:
            await previous.close(code=4409, reason="replaced by a newer connection")
        except RuntimeError:
            pass
    st.active_sockets[user.user_id] = websocket

    async def send(msg: Any) -> None:
        await websocket.send_text(msg.model_dump_json())

    run_id = ctx["session"]["run_id"]

    def tool_ctx(budget: TokenBudget) -> ToolContext:
        return ToolContext(
            mode="live",
            now=_now(),
            run_id=run_id,
            user_id=user.user_id,
            budget=budget,
            trace=TraceCtx(run_id=run_id, user_id=user.user_id, role="voice.quick_search"),
        )

    prefs = ctx["settings"]
    cfg = VoiceConfig.from_agent_config(loader.agent(), **st.voice_overrides)
    if "auto_resume" in prefs:
        cfg.auto_resume = bool(prefs["auto_resume"])
    session = VoiceSession(
        session_id=session_id,
        user_id=user.user_id,
        brief=ctx["brief"],
        packs=ctx["packs"],
        model=st.voice_model,
        tools=st.tools,
        store=DbSessionStore(st.db, user.user_id, session_id, run_id=run_id),
        send=send,
        tool_ctx=tool_ctx,
        tts=st.tts,
        stt=st.stt,
        config=cfg,
        run_id=run_id,
        open_questions=ctx["open_questions"],
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = _client_msg.validate_json(raw)
            except ValidationError as e:
                await send(ErrorMsg(code="bad_message", message=str(e.errors(include_url=False))[:300]))
                continue
            await session.handle(msg)
            if session.ended:
                break
    except WebSocketDisconnect:
        pass
    except RuntimeError as e:  # closed by a newer connection
        log.info("session %s socket closed: %s", session_id, e)
    finally:
        if not session.ended:
            await asyncio.shield(session.end("natural" if session.brief_completed else "cut_short"))
        if st.active_sockets.get(user.user_id) is websocket:
            del st.active_sockets[user.user_id]
        if session.ended:
            try:
                await websocket.close()
            except RuntimeError:
                pass
