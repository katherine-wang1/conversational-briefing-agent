"""Job handlers (TDD §20). Each sets the acting user (or '') through db scopes before any access.

Handlers call the agent entry points through `deps.ports` (real agents once W-C/W-D are merged,
StubAgents otherwise; see app/jobs/ports.py).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from app.config import loader
from app.contracts.brief import BriefScript
from app.contracts.digest import Digest, DigestEntry, StoryFeatures
from app.contracts.memory import SessionContext
from app.contracts.sources import HeadlineObservation
from app.db.ids import new_id
from app.db.memory_io import load_memory, row_to_signal, save_memory
from app.jobs.deps import Deps
from app.jobs.ports import UserCtx
from app.jobs.queue import ON_DEMAND_PRIORITY, Job
from app.speech.render import render_brief_audio

log = logging.getLogger(__name__)

Handler = Callable[[Deps, Job], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


class PermanentJobError(Exception):
    """Don't retry (bad payload, missing user, …)."""


def handler(name: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn

    return deco


def budget_for(name: str) -> dict[str, Any]:
    raw = (loader.agent().get("budgets") or {}).get(name, {})
    return {k: v for k, v in raw.items() if k in ("input_tokens", "output_tokens", "searches", "fetches")}


def flags_for(arm: str) -> Any:
    try:
        return loader.experiment(arm).flags
    except FileNotFoundError:
        return loader.experiment("full").flags


def local_today(tz: str, now: datetime) -> date:
    try:
        return now.astimezone(ZoneInfo(tz)).date()
    except (KeyError, ValueError):
        return now.astimezone(UTC).date()


def _need_user(job: Job) -> UUID:
    if job.user_id is None:
        raise PermanentJobError(f"{job.type} needs user_id")
    return job.user_id


# ---- newsroom -----------------------------------------------------------------------------------------


def digest_from_rows(d: dict[str, Any], entries: list[dict[str, Any]]) -> Digest:
    return Digest(
        digest_id=d["id"],
        run_id=d["run_id"],
        beat=d["beat"],
        at=d["at"],
        version=d["version"],
        entries=[
            DigestEntry(
                story_id=e["story_id"],
                headline=e["headline"],
                summary=e["summary"],
                subtopics=list(e["subtopics"] or []),
                entities=list(e["entities"] or []),
                importance=e["importance"],
                evidence=[str(x) for x in e["evidence"] or []],
                is_update_of=e["is_update_of"],
                importance_rules=e["importance_rules"],
                features=StoryFeatures.model_validate(e["features"] or {}),
                urls=list(e["urls"] or []),
            )
            for e in entries
        ],
    )


async def save_digest(s: Any, digest: Digest) -> None:
    await s.digests.upsert(
        id=digest.digest_id, run_id=digest.run_id, beat=digest.beat, at=digest.at, version=digest.version
    )
    for e in digest.entries:
        await s.digest_entries.upsert(
            digest_id=digest.digest_id,
            story_id=e.story_id,
            headline=e.headline,
            summary=e.summary,
            subtopics=e.subtopics,
            entities=e.entities,
            importance=e.importance,
            importance_rules=e.importance_rules,
            evidence=e.evidence,
            is_update_of=e.is_update_of,
            features=e.features,
            urls=e.urls,
        )


@handler("newsroom_run")
async def newsroom_run(deps: Deps, job: Job) -> None:
    beat_id = job.payload["beat"]
    at = datetime.fromisoformat(job.payload["at"])
    beat = loader.beats().get(beat_id)
    if beat is None:
        raise PermanentJobError(f"unknown beat {beat_id}")
    run_row_id = new_id("nr")
    async with deps.db.system_scope() as s:
        await s.newsroom_runs.insert(id=run_row_id, beat=beat_id, at=at, status="running")
    ctx = deps.tool_ctx(budget=budget_for("newsroom_run"), job_id=job.id, role="newsroom")
    observations: list[HeadlineObservation] = []
    for src in beat.headline_sources:
        r = await deps.tools["headline_source"](ctx, source_id=src.id, at=at)
        if r.ok:
            observations.extend(r.data.observations)
        else:
            log.warning("newsroom %s: source %s skipped: %s", beat_id, src.id, r.error)
    if not observations:
        # Don't publish an empty digest over the last good one (e.g. source adapters not wired yet).
        async with deps.db.system_scope() as s:
            await s.newsroom_runs.update(run_row_id, status="no_observations")
        log.warning("newsroom %s at %s: no observations from any source; digest not updated", beat_id, at)
        return
    async with deps.db.system_scope() as s:
        for o in observations:
            await s.headline_observations.insert(id=new_id("ob"), **o.model_dump())
    digest, _trace = await deps.ports.run_newsroom(
        beat_id, at, observations, deps.model_client, deps.tools, flags_for("full")
    )
    async with deps.db.system_scope() as s:
        await save_digest(s, digest)
        await s.newsroom_runs.update(run_row_id, status="done")


# ---- chief of staff -------------------------------------------------------------------------------------


async def latest_digests(deps: Deps, at: datetime) -> list[Digest]:
    out = []
    async with deps.db.system_scope() as s:
        for beat_id in loader.beats():
            d = await s.digests.latest(beat_id, at)
            if d:
                out.append(digest_from_rows(d, await s.digest_entries.list(digest_id=d["id"])))
    return out


@handler("cos_daily")
async def cos_daily(deps: Deps, job: Job) -> None:
    uid = _need_user(job)
    now = deps.clock()
    arm = job.payload.get("arm", "full")
    async with deps.db.user_scope(uid) as s:
        prof = await s.profiles.me()
        if prof is None:
            raise PermanentJobError("user no longer exists")
        local_date = (
            date.fromisoformat(job.payload["local_date"])
            if job.payload.get("local_date")
            else local_today(prof["timezone"], now)
        )
        existing = await s.briefs.find_one(local_date=local_date, arm=arm, run_id="live")
        if existing and existing["status"] in ("ready", "rendering"):
            return
        memory = await load_memory(s, local_date)
        past = await s.briefs.list(order_by="local_date", desc=True, limit=15)
        history = [
            {
                "brief_id": b["id"],
                "local_date": b["local_date"].isoformat(),
                "stories": (b["script"] or {}).get("stories", []),
            }
            for b in past
            if b["local_date"] < local_date
        ]
        questions = await s.open_questions.list(status=["answered", "unanswerable"])
    brief_id = existing["id"] if existing else new_id("br")
    user_ctx = UserCtx(
        user_id=str(uid),
        run_id="live",
        local_date=local_date,
        timezone=prof["timezone"],
        brief_id=brief_id,
        arm=arm,
        settings=prof["settings"],
    )
    script, packs, _trace = await deps.ports.run_cos_daily(
        user_ctx,
        await latest_digests(deps, now),
        memory,
        history,
        questions,
        flags_for(arm),
        deps.model_client,
        deps.tools,
    )
    script = script.model_copy(update={"brief_id": brief_id, "user_id": str(uid)})
    async with deps.db.user_scope(uid) as s:
        await s.briefs.upsert(
            conflict=("user_id", "run_id", "local_date", "arm"),
            id=brief_id,
            local_date=local_date,
            arm=arm,
            status="rendering",
            script=script,
        )
        for p in packs:
            await s.source_packs.upsert(brief_id=brief_id, story_id=p.story_ref, pack=p)
        if questions:
            await s.open_questions.update_where({"id": [q["id"] for q in questions]}, status="delivered")
    await deps.queue.enqueue(
        "tts_render", {"brief_id": brief_id}, user_id=uid, dedupe_key=f"tts:{brief_id}", priority=job.priority
    )


@handler("tts_render")
async def tts_render(deps: Deps, job: Job) -> None:
    uid = _need_user(job)
    async with deps.db.user_scope(uid) as s:
        b = await s.briefs.get(job.payload["brief_id"])
    if b is None:
        raise PermanentJobError("brief not found")
    manifest = None
    if deps.tts is not None:
        script = BriefScript.model_validate(b["script"])
        manifest = await render_brief_audio(script, deps.tts, deps.storage, voice=None)
    else:
        log.info("tts_render %s: no TTS provider configured; brief served without audio", b["id"])
    async with deps.db.user_scope(uid) as s:
        await s.briefs.update(b["id"], status="ready", audio_manifest=manifest, ready_at=deps.clock())


# ---- memory / sessions ------------------------------------------------------------------------------------


@handler("memory_update")
async def memory_update(deps: Deps, job: Job) -> None:
    uid = _need_user(job)
    sid = job.payload["session_id"]
    async with deps.db.user_scope(uid) as s:
        sess = await s.sessions.get(sid)
        if sess is None:
            raise PermanentJobError("session not found")
        turns = await s.turns.list(session_id=sid)
        signals = [row_to_signal(r) for r in await s.memory_signals.list(session_id=sid)]
        brief = await s.briefs.get(sess["brief_id"]) if sess["brief_id"] else None
        memory = await load_memory(s, deps.clock().date())
    shown = [st["story_ref"] for st in ((brief or {}).get("script") or {}).get("stories", [])]
    transcript = [
        {k: t[k] for k in ("id", "role", "text", "story_ref", "action", "citations")} for t in turns
    ]
    ctx = SessionContext(
        time_available_s=sess["time_available_s"],
        end_reason=sess["end_reason"],
        brief_length_s=((brief or {}).get("script") or {}).get("est_duration_s"),
    )
    new_memory = await deps.ports.memory_update(
        memory,
        transcript,
        signals,
        shown,
        ctx,
        flags_for((brief or {}).get("arm", "full")),
        deps.model_client,
    )
    async with deps.db.user_scope(uid) as s:
        await save_memory(s, new_memory)


@handler("followup_research")
async def followup_research(deps: Deps, job: Job) -> None:
    uid = _need_user(job)
    qid = job.payload["question_id"]
    async with deps.db.user_scope(uid) as s:
        q = await s.open_questions.update(qid, status="researching")
    if q is None:
        raise PermanentJobError("question not found")
    result = await deps.ports.run_followup(
        {k: q[k] for k in ("id", "question", "context", "story_ref", "created_at")},
        deps.model_client,
        deps.tools,
    )
    status = "answered" if result.get("status") == "answered" else "unanswerable"
    cites = [c for sent in result.get("answer_sentences", []) for c in sent.get("cites", [])]
    async with deps.db.user_scope(uid) as s:
        await s.open_questions.update(
            qid, status=status, answer=result, citations=cites, answered_at=deps.clock()
        )


@handler("onboarding_seed")
async def onboarding_seed(deps: Deps, job: Job) -> None:
    uid = _need_user(job)
    interests = await deps.ports.extract_onboarding(uid, job.payload.get("text", ""), deps.model_client)
    async with deps.db.user_scope(uid) as s:
        for i in interests:
            await s.interests.upsert(conflict=("user_id", "run_id", "id"), **i.model_dump())
        prof = await s.profiles.me()
    if prof is None:
        raise PermanentJobError("user no longer exists")
    d = local_today(prof["timezone"], deps.clock())
    await deps.queue.enqueue(
        "cos_daily",
        {"local_date": d.isoformat()},
        user_id=uid,
        dedupe_key=f"cos:{uid}:{d}",
        priority=ON_DEMAND_PRIORITY,
    )


@handler("daily_snapshot")
async def daily_snapshot(deps: Deps, job: Job) -> None:
    uid = _need_user(job)
    d = date.fromisoformat(job.payload["local_date"])
    async with deps.db.user_scope(uid) as s:
        snap = await load_memory(s, d)
        await s.memory_snapshots.upsert(
            conflict=("user_id", "run_id", "as_of_date"), as_of_date=d, payload=snap
        )


# ---- system ------------------------------------------------------------------------------------------------


@handler("cleanup")
async def cleanup(deps: Deps, job: Job) -> None:
    now = deps.clock()
    n_audio = await deps.storage.delete_older_than(now - timedelta(days=7))  # TDD §10.4
    async with deps.db.system_scope() as s:
        n_jobs = await s.execute(
            "delete from jobs where status in ('done', 'cancelled') and finished_at < %s",
            [now - timedelta(days=14)],
        )
        await s.execute("delete from worker_heartbeats where seen_at < %s", [now - timedelta(days=1)])
    log.info("cleanup: %s audio objects, %s jobs removed", n_audio, n_jobs)


@handler("spend_guard")
async def spend_guard(deps: Deps, job: Job) -> None:
    cfg = loader.agent().get("spend_guard", {})
    thresholds = sorted(cfg.get("alert_pct", [40, 60, 80]))
    pause_at = cfg.get("pause_eval_at_pct", 60)
    credits = {"openrouter": deps.settings.credit_openrouter_usd, "tinker": deps.settings.credit_tinker_usd}
    paused: list[str] = []
    async with deps.db.system_scope() as s:
        spend = await s.llm_calls.spend_by_provider()
        for provider, usd in spend.items():
            credit = credits.get(provider)
            if not credit:
                continue
            pct = 100.0 * usd / credit
            for t in thresholds:
                if pct >= t:
                    await s.execute(
                        "insert into admin_alerts (key, kind, message) values (%s, 'spend', %s)"
                        " on conflict (key) do nothing",
                        [
                            f"spend:{provider}:{t}",
                            f"{provider} spend ${usd:.2f} is {pct:.0f}% of ${credit:.0f}",
                        ],
                    )
            if thresholds and pct >= thresholds[0]:
                log.warning("spend_guard: %s at %.0f%% of credit", provider, pct)
            if pct >= pause_at:
                paused.append(provider)
    if paused:
        n = await deps.queue.pause_types("eval_%")
        log.warning("spend_guard: paused %s queued eval jobs (%s over %s%%)", n, paused, pause_at)


@handler("delete_user")
async def delete_user(deps: Deps, job: Job) -> None:
    """Runs after DELETE /api/me removed the per-user rows: purge storage + ledger links + auth user."""
    uid = _need_user(job)
    await deps.storage.delete_prefix(f"{uid}/")
    async with deps.db.system_scope() as s:
        await s.execute("update llm_calls set user_id = null where user_id = %s", [uid])
    await deps.queue.cancel_for_user(uid)
    st = deps.settings
    if st.supabase_url and st.supabase_service_role_key:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.delete(
                f"{st.supabase_url.rstrip('/')}/auth/v1/admin/users/{uid}",
                headers={
                    "Authorization": f"Bearer {st.supabase_service_role_key}",
                    "apikey": st.supabase_service_role_key,
                },
            )
        if r.status_code not in (200, 204, 404):
            raise RuntimeError(f"auth user deletion failed: HTTP {r.status_code}")
