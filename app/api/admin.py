"""Admin routes (TDD §11, §10.3). They connect as `app_admin`, which can read profiles, jobs and
llm_calls only (no user content), manage invites, and retry jobs. Every action is audited."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import CurrentUser
from app.api.deps import admin_user

router = APIRouter(prefix="/api/admin")
Admin = Depends(admin_user)


class InviteIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    role: Literal["user", "admin"] = "user"


def _admin_db(request: Request):  # noqa: ANN202
    db = request.app.state.admin_db
    if db is None:
        raise HTTPException(503, "admin database not configured (DATABASE_ADMIN_URL)")
    return db


async def _audit(s: Any, admin: CurrentUser, action: str, target: Any = None, **detail: Any) -> None:
    await s.admin_audit.insert(admin_id=admin.user_id, action=action, target_user=target, detail=detail)


@router.post("/invites", status_code=201)
async def invite(body: InviteIn, request: Request, admin: CurrentUser = Admin) -> dict[str, Any]:
    async with _admin_db(request).system_scope() as s:
        row = await s.invites.upsert(email=body.email.lower(), role=body.role, invited_by=admin.user_id)
        await _audit(s, admin, "invite", email=body.email.lower(), role=body.role)
    return row


@router.get("/invites")
async def list_invites(request: Request, admin: CurrentUser = Admin) -> dict[str, Any]:
    async with _admin_db(request).system_scope() as s:
        return {"invites": await s.invites.list(order_by="created_at", desc=True)}


@router.get("/users")
async def users(request: Request, admin: CurrentUser = Admin) -> dict[str, Any]:
    async with _admin_db(request).system_scope() as s:
        rows = await s.fetch_all(
            "select user_id, email, display_name, status, type, role, research_consent, timezone, brief_time,"
            " created_at from profiles where run_id = 'live' order by created_at"
        )
        await _audit(s, admin, "list_users")
    return {"users": rows}


@router.get("/jobs")
async def jobs(
    request: Request, status: str | None = None, limit: int = Query(100, le=500), admin: CurrentUser = Admin
) -> dict[str, Any]:
    async with _admin_db(request).system_scope() as s:
        rows = await s.fetch_all(
            "select id, type, dedupe_key, user_id, status, priority, attempts, max_attempts, run_at,"
            " last_error, created_at, finished_at from jobs"
            " where (%s::text is null or status = %s) order by id desc limit %s",
            [status, status, limit],
        )
    return {"jobs": rows}


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: int, request: Request, admin: CurrentUser = Admin) -> dict[str, Any]:
    async with _admin_db(request).system_scope() as s:
        rows = await s.fetch_all(
            "update jobs set status = 'queued', attempts = 0, run_at = now(), last_error = null,"
            " locked_by = null"
            " where id = %s and status in ('failed', 'paused', 'cancelled') returning id, status",
            [job_id],
        )
        if not rows:
            raise HTTPException(404, "no failed/paused job with that id")
        await _audit(s, admin, "retry_job", job_id=job_id)
    return rows[0]


@router.get("/costs")
async def costs(request: Request, days: int = Query(30, ge=1, le=365), admin: CurrentUser = Admin) -> dict:
    async with _admin_db(request).system_scope() as s:
        by_day = await s.fetch_all(
            "select date_trunc('day', created_at)::date as day, role, provider, count(*) as calls,"
            " sum(tokens_in) as tokens_in, sum(tokens_out) as tokens_out,"
            " coalesce(sum(cost_usd), 0)::float as cost_usd"
            " from llm_calls where created_at > now() - make_interval(days => %s)"
            " group by 1, 2, 3 order by 1 desc, 5 desc",
            [days],
        )
        by_user = await s.fetch_all(
            "select user_id, run_id, coalesce(sum(cost_usd), 0)::float as cost_usd, count(*) as calls"
            " from llm_calls where created_at > now() - make_interval(days => %s)"
            " group by 1, 2 order by 3 desc",
            [days],
        )
        alerts = await s.admin_alerts.list(order_by="created_at", desc=True, limit=20)
    return {"by_day": by_day, "by_user": by_user, "alerts": alerts}
