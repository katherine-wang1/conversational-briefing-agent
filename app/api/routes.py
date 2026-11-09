"""User-facing REST routes (TDD §11). Every handler works inside the caller's user scope (RLS)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.agents.voice.citations import CitationIndex, load_titles, pack_doc_ids
from app.api.auth import CurrentUser
from app.api.deps import current_user
from app.contracts.brief import SourcePack
from app.db.ids import new_id
from app.db.repos import USER_TABLES
from app.jobs.handlers import local_today, news_pending
from app.jobs.queue import ON_DEMAND_PRIORITY, JobQueue

router = APIRouter(prefix="/api")
User = Depends(current_user)

PREF_KEYS = ("length_s", "depth", "technicality", "style", "preferred_sources")
SETTINGS_KEYS = {"voice", "stt_mode", "auto_resume", "headphones_mode", "tts", "playback_rate", "show_debug"}
PLAYBACK_RATES = (0.8, 2.0)  # allowed playback_rate range (inclusive)
DEFAULT_PLAYBACK_RATE = 1.2  # TODO 1.21: the voice is a bit slow at 1.0


def _db(request: Request):  # noqa: ANN202
    return request.app.state.db


def _queue(request: Request) -> JobQueue:
    return request.app.state.queue


def _validate_tz(v: str | None) -> str | None:
    if v is None:
        return v
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown timezone {v!r}") from e
    return v


class MePatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    timezone: str | None = None
    brief_time: time | None = None
    research_consent: bool | None = None
    settings: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None

    check_tz = field_validator("timezone")(_validate_tz)


class Onboarding(BaseModel):
    interests_text: str = Field(min_length=1, max_length=4000)
    length_s: int | None = Field(default=None, ge=60, le=1800)
    depth: Literal["light", "standard", "deep"] | None = None
    technicality: Literal["low", "medium", "high"] | None = None
    brief_time: time | None = None
    timezone: str | None = None
    research_consent: bool = False
    display_name: str | None = Field(default=None, max_length=100)
    # W-F OnboardingRequest aliases
    length_pref: Literal["short", "medium", "long"] | None = None
    depth_pref: Literal["light", "standard", "deep"] | None = None

    check_tz = field_validator("timezone")(_validate_tz)


LENGTH_PREF_S = {"short": 180, "medium": 300, "long": 600}


class SessionCreate(BaseModel):
    brief_id: str | None = None
    time_available_s: int | None = Field(default=None, ge=0, le=7200)


class InterestPatch(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    weight: float | None = Field(default=None, ge=0, le=10)
    explicit_pin: bool | None = None


class QuestionPatch(BaseModel):
    status: Literal["dismissed"]


class RatingIn(BaseModel):
    session_id: str | None = None
    usefulness: int | None = Field(default=None, ge=1, le=5)
    per_story: dict[str, Any] = {}
    learned_new: bool | None = None
    length_fit: Literal["too_short", "right", "about_right", "too_long"] | None = None
    followup_useful: int | None = Field(default=None, ge=1, le=5)


# ---- me ----


@router.get("/me")
async def get_me(request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        prof = await s.profiles.me()
        prefs = await s.delivery_prefs.list(run_id="live")
        open_q = await s.open_questions.count(status=["open", "researching", "answered"])
    delivery = {p["key"]: p["value"] for p in prefs}
    assert prof is not None
    st = prof.get("settings") or {}
    return {
        # W-F `Me` shape (flat)
        "user_id": prof["user_id"],
        "email": prof["email"],
        "display_name": prof["display_name"],
        "onboarded": prof["status"] != "onboarding",
        "research_consent": prof["research_consent"],
        "settings": {
            "brief_time": prof["brief_time"].strftime("%H:%M"),
            "timezone": prof["timezone"],
            "voice": st.get("voice", "alloy"),
            "stt_mode": st.get("stt_mode", "browser"),
            "auto_resume": st.get("auto_resume", True),
            "headphones_mode": st.get("headphones_mode", False),
            "playback_rate": st.get("playback_rate", DEFAULT_PLAYBACK_RATE),
            "show_debug": st.get("show_debug", False),
            "depth_pref": delivery.get("depth"),
        },
        # full detail
        "profile": prof,
        "delivery": delivery,
        "open_questions": open_q,
    }


async def _set_prefs(s: Any, values: dict[str, Any], confidence: float) -> None:
    for k, v in values.items():
        if k not in PREF_KEYS:
            raise HTTPException(422, f"unknown delivery preference {k!r}")
        if v is not None:
            await s.delivery_prefs.set(k, v, confidence)


def _check_settings(st: dict[str, Any]) -> None:
    if "playback_rate" in st:
        r = st["playback_rate"]
        lo, hi = PLAYBACK_RATES
        if isinstance(r, bool) or not isinstance(r, int | float) or not lo <= r <= hi:
            raise HTTPException(422, f"playback_rate must be a number from {lo} to {hi}")
    if "show_debug" in st and not isinstance(st["show_debug"], bool):
        raise HTTPException(422, "show_debug must be true or false")


@router.patch("/me")
async def patch_me(body: MePatch, request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        fields: dict[str, Any] = body.model_dump(
            exclude_none=True, include={"display_name", "timezone", "brief_time", "research_consent"}
        )
        if body.settings is not None:
            bad = set(body.settings) - SETTINGS_KEYS
            if bad:
                raise HTTPException(422, f"unknown settings {sorted(bad)}")
            _check_settings(body.settings)
            fields["settings"] = {**(user.profile.get("settings") or {}), **body.settings}
        if fields:
            fields["updated_at"] = datetime.now(UTC)
            await s.profiles.update(user.user_id, **fields)
        if body.delivery:
            await _set_prefs(s, body.delivery, 1.0)  # set by the user: explicit
    return await get_me(request, user)


@router.delete("/me", status_code=202)
async def delete_me(request: Request, user: CurrentUser = User) -> dict[str, str]:
    """Delete the account (DATA-6): per-user rows go now (cascade); a job purges audio + auth user."""
    async with _db(request).user_scope(user.user_id) as s:
        await s.profiles.delete(user.user_id)
    await _queue(request).enqueue("delete_user", user_id=user.user_id, dedupe_key=f"del:{user.user_id}")
    return {"status": "deleted"}


@router.post("/onboarding", status_code=202)
async def onboarding(body: Onboarding, request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        fields: dict[str, Any] = {
            "status": "active",
            "research_consent": body.research_consent,
            "updated_at": datetime.now(UTC),
        }
        for k in ("timezone", "brief_time", "display_name"):
            if getattr(body, k) is not None:
                fields[k] = getattr(body, k)
        await s.profiles.update(user.user_id, **fields)
        length_s = body.length_s or LENGTH_PREF_S.get(body.length_pref or "")
        depth = body.depth or body.depth_pref
        await _set_prefs(s, {"length_s": length_s, "depth": depth, "technicality": body.technicality}, 0.5)
        await s.topic_requests.insert(text=body.interests_text)
    await _queue(request).enqueue(
        "onboarding_seed",
        {"text": body.interests_text},
        user_id=user.user_id,
        dedupe_key=f"onboard:{user.user_id}:{new_id()}",
        priority=ON_DEMAND_PRIORITY,
    )
    return {"status": "seeding", "next": "/api/briefs/today"}


# ---- briefs ----


async def brief_view(request: Request, b: dict[str, Any]) -> dict[str, Any]:
    manifest = b.get("audio_manifest")
    if manifest:
        storage = request.app.state.storage
        manifest = {
            **manifest,
            "segments": [
                {**seg, "url": await storage.url(seg["url"])} for seg in manifest.get("segments", [])
            ],
        }
    return {
        "id": b["id"],
        "brief_id": b["id"],  # W-F TodayBrief shape
        "local_date": b["local_date"],
        "status": b["status"],
        "arm": b["arm"],
        "script": b["script"],
        "audio_manifest": manifest,
        "manifest": manifest,  # W-F TodayBrief shape
        "ready_at": b.get("ready_at"),
    }


@router.get("/briefs/today")
async def brief_today(request: Request, response: Response, user: CurrentUser = User) -> dict[str, Any]:
    d = local_today(user.profile["timezone"], datetime.now(UTC))
    async with _db(request).user_scope(user.user_id) as s:
        b = await s.briefs.find_one(local_date=d, run_id="live", status=["ready", "fallback"])
        pending = None if b else await s.briefs.find_one(local_date=d, run_id="live")
    if b:
        return await brief_view(request, b)
    if user.profile["status"] == "onboarding":
        response.status_code = 409
        return {"status": "onboarding_required", "local_date": d}
    await _queue(request).enqueue(
        "cos_daily",
        {"local_date": d.isoformat()},
        user_id=user.user_id,
        dedupe_key=f"cos:{user.user_id}:{d}",
        priority=ON_DEMAND_PRIORITY,
    )
    response.status_code = 202
    # TODO(TDD §20): after 3 min, serve the importance-only quick brief from the digest (B1 logic).
    failed = (pending or {}).get("status") == "failed"
    async with _db(request).system_scope() as s:
        gathering = await news_pending(s, datetime.now(UTC))
    return {
        "brief_id": (pending or {}).get("id"),
        "status": "failed" if failed else "pending",  # W-F: pending | ready | failed
        "script": None,
        "manifest": None,
        "local_date": d,
        # Additive: what the worker is doing, so the app can say so while it waits.
        "stage": "gathering_news" if gathering else "writing",
        "message": "Gathering today's news" if gathering else "Writing your brief",
    }


@router.get("/briefs/{brief_id}")
async def brief_by_id(brief_id: str, request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        b = await s.briefs.get(brief_id)
    if b is None:
        raise HTTPException(404, "not_found")
    return await brief_view(request, b)


# ---- sessions / history ----


def ws_base(request: Request) -> str:
    base = request.app.state.settings.public_base_url or str(request.base_url)
    return base.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")


@router.post("/sessions", status_code=201)
async def create_session(body: SessionCreate, request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        if body.brief_id and await s.briefs.get(body.brief_id) is None:
            raise HTTPException(404, "brief_not_found")
        sid = new_id("se")
        await s.sessions.insert(id=sid, brief_id=body.brief_id, time_available_s=body.time_available_s)
    return {"session_id": sid, "ws_url": f"{ws_base(request)}/ws/session/{sid}"}


@router.get("/history")
async def history(request: Request, days: int = Query(14, ge=1, le=90), user: CurrentUser = User) -> dict:
    since = date.today() - timedelta(days=days)
    async with _db(request).user_scope(user.user_id) as s:
        briefs = await s.fetch_all(
            "select id, local_date, status, arm, script->'stories' as stories from briefs"
            " where run_id = 'live' and local_date >= %s order by local_date desc",
            [since],
        )
        sessions = await s.fetch_all(
            "select s.id, s.brief_id, s.started_at, s.ended_at, s.end_reason,"
            " (select count(*) from turns t where t.session_id = s.id and t.role <> 'system') as n_turns"
            " from sessions s where s.run_id = 'live' and s.started_at >= %s order by s.started_at desc",
            [datetime.combine(since, time(0), UTC)],
        )
    return {"briefs": briefs, "sessions": sessions}


@router.get("/sessions/{session_id}/transcript")
async def transcript(session_id: str, request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        sess = await s.sessions.get(session_id)
        if sess is None:
            raise HTTPException(404, "not_found")
        turns = await s.turns.list(session_id=session_id)
        cited = any(t.get("citations") for t in turns)
        rows = await s.source_packs.list(brief_id=sess["brief_id"]) if cited and sess["brief_id"] else []
        packs = [SourcePack.model_validate(p["pack"]) for p in rows]
        index = CitationIndex(packs, await load_titles(s, pack_doc_ids(packs)))
    # Additive (TODO 1.22): `sources` = the turn's `citations` resolved to {source, url, title}.
    return {
        "turns": [
            {**t, "sources": [c.model_dump() for c in index.resolve_many(t.get("citations") or [])]}
            for t in turns
        ]
    }


# ---- memory / questions / ratings ----


@router.get("/memory/interests")
async def list_interests(request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        return {"interests": await s.interests.list(run_id="live", order_by="weight", desc=True)}


@router.patch("/memory/interests/{interest_id}")
async def patch_interest(interest_id: str, body: InterestPatch, request: Request, user: CurrentUser = User):  # noqa: ANN201
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(422, "nothing to update")
    async with _db(request).user_scope(user.user_id) as s:
        rows = await s.interests.update_where(
            {"id": interest_id, "run_id": "live"}, **fields, updated_at=datetime.now(UTC)
        )
    if not rows:
        raise HTTPException(404, "not_found")
    return rows[0]


@router.delete("/memory/interests/{interest_id}", status_code=204)
async def delete_interest(interest_id: str, request: Request, user: CurrentUser = User) -> Response:
    async with _db(request).user_scope(user.user_id) as s:
        n = await s.interests.delete_where(id=interest_id, run_id="live")
    if not n:
        raise HTTPException(404, "not_found")
    return Response(status_code=204)


@router.get("/questions")
async def list_questions(request: Request, status: str | None = None, user: CurrentUser = User) -> dict:
    async with _db(request).user_scope(user.user_id) as s:
        where = {"status": status} if status else {}
        return {
            "questions": await s.open_questions.list(run_id="live", order_by="created_at", desc=True, **where)
        }


@router.patch("/questions/{question_id}")
async def patch_question(question_id: str, body: QuestionPatch, request: Request, user: CurrentUser = User):  # noqa: ANN201
    async with _db(request).user_scope(user.user_id) as s:
        row = await s.open_questions.update(question_id, status=body.status)
    if row is None:
        raise HTTPException(404, "not_found")
    return row


@router.post("/ratings", status_code=201)
async def post_rating(body: RatingIn, request: Request, user: CurrentUser = User) -> dict[str, Any]:
    async with _db(request).user_scope(user.user_id) as s:
        if body.session_id and await s.sessions.get(body.session_id) is None:
            raise HTTPException(404, "session_not_found")
        return await s.ratings.insert(id=new_id("ra"), **body.model_dump())


# ---- export ----


@router.get("/export")
async def export(request: Request, user: CurrentUser = User) -> dict[str, Any]:
    """Everything stored about the caller (DATA-6), all runs, as JSON."""
    out: dict[str, Any] = {"exported_at": datetime.now(UTC), "user_id": user.user_id}
    async with _db(request).user_scope(user.user_id) as s:
        for t in USER_TABLES:
            out[t] = await s.repo(t).list()
    return out
