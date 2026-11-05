"""Web process: `uvicorn app.main:app` (Railway `web` service).

REST API (/api/*), the live session WebSocket (/ws/session/{id}), /healthz, dev audio under /media,
and the built PWA (web/dist) at / when present.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents.voice import ws as voice_ws
from app.agents.voice.fake_model import CorpusEchoModel, UnavailableModel
from app.api import admin, routes
from app.api.auth import JwtVerifier
from app.api.deps import RateLimiter
from app.config.settings import Settings, get_settings
from app.db.pool import Database
from app.jobs.ports import load_model_client
from app.jobs.queue import JobQueue
from app.speech import build_storage, build_stt, build_tts
from app.tools import build_tools

log = logging.getLogger("app")
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
_UNSET: Any = object()


def pick_voice_model(settings: Settings) -> Any:
    if settings.voice_model == "fake":
        log.warning("VOICE_MODEL=fake: using the rule-based CorpusEchoModel (dev only, not an agent)")
        return CorpusEchoModel()
    client = load_model_client()
    if client is None:
        log.warning("no model client available (app/llm not wired); voice turns will report an error")
        return UnavailableModel()
    return client


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    admin_db: Database | None = _UNSET,
    voice_model: Any = None,
    tools: dict[str, Any] | None = None,
    tts: Any = _UNSET,
    stt: Any = _UNSET,
    storage: Any = None,
    voice_overrides: dict[str, Any] | None = None,
) -> FastAPI:
    s = settings or get_settings()
    owns_db = db is None
    owns_admin = admin_db is _UNSET

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        st = app.state
        if owns_db:
            st.db = Database(s.database_url, min_size=s.db_pool_min, max_size=s.db_pool_max, name="web")
            await st.db.open()
        if owns_admin:
            st.admin_db = None
            if s.database_admin_url:
                st.admin_db = Database(s.database_admin_url, min_size=1, max_size=2, admin=True, name="admin")
                await st.admin_db.open()
        st.queue = JobQueue(st.db)
        if st.tools is None:
            st.tools = build_tools(s, db=st.db)
        from app.llm.factory import attach_db_ledger, flush_ledger

        if attach_db_ledger(st.voice_model, st.db):  # voice.turn calls -> llm_calls too
            log.info("voice model ledger: JSONL + llm_calls")
        try:
            yield
        finally:
            await flush_ledger(st.voice_model)
            if owns_admin and st.admin_db is not None:
                await st.admin_db.close()
            if owns_db:
                await st.db.close()

    app = FastAPI(title="Briefing agent", version="0.1.0", lifespan=lifespan)
    st = app.state
    st.settings = s
    st.db = db
    st.admin_db = None if admin_db is _UNSET else admin_db
    st.tools = tools
    st.voice_model = voice_model or pick_voice_model(s)
    st.tts = build_tts(s) if tts is _UNSET else tts
    st.stt = build_stt(s) if stt is _UNSET else stt
    st.storage = storage or build_storage(s)
    st.verifier = JwtVerifier(s)
    st.rate_limiter = RateLimiter(s.rate_limit_per_min)
    st.active_sockets = {}
    st.voice_overrides = voice_overrides or {}

    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        ok = True
        try:
            async with app.state.db.system_scope() as sc:
                await sc.fetch_one("select 1 as x")
        except Exception:  # noqa: BLE001
            ok = False
        return {"ok": ok, "db": ok, "env": s.app_env}

    app.include_router(routes.router)
    app.include_router(admin.router)
    app.include_router(voice_ws.router)

    if s.audio_storage == "local":
        media = Path(s.audio_local_dir)
        media.mkdir(parents=True, exist_ok=True)
        app.mount("/media", StaticFiles(directory=media), name="media")
    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


def _lazy_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()


def __getattr__(name: str) -> Any:  # `uvicorn app.main:app` builds the app on first access
    if name == "app":
        globals()["app"] = _lazy_app()
        return globals()["app"]
    raise AttributeError(name)
