"""Postgres job queue (TDD §20): dedupe keys, priorities, retries with exponential backoff.

Workers claim with `FOR UPDATE SKIP LOCKED`, so any number of workers can share the table.
Lower `priority` runs first. All queue access happens in a system scope (app.user_id = '').
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.db.pool import Database


@dataclass(frozen=True)
class JobSpec:
    priority: int
    max_attempts: int


# TDD §20. max_attempts = 1 + retries.
JOB_SPECS: dict[str, JobSpec] = {
    "spend_guard": JobSpec(1, 1),
    "followup_research": JobSpec(2, 3),
    "memory_update": JobSpec(3, 4),
    "cos_daily": JobSpec(4, 3),
    "tts_render": JobSpec(4, 4),
    "newsroom_run": JobSpec(5, 4),
    "onboarding_seed": JobSpec(3, 3),
    "daily_snapshot": JobSpec(6, 4),
    "delete_user": JobSpec(2, 5),
    "cleanup": JobSpec(9, 2),
}
ON_DEMAND_PRIORITY = 0
BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 3600


class Job(BaseModel):
    id: int
    type: str
    payload: dict[str, Any] = {}
    user_id: UUID | None = None
    run_id: str = "live"
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 5
    dedupe_key: str | None = None
    status: str = "queued"
    last_error: str | None = None


def backoff_s(attempts: int) -> int:
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, attempts - 1))


class JobQueue:
    def __init__(self, db: Database):
        self.db = db

    async def enqueue(
        self,
        type: str,
        payload: dict[str, Any] | None = None,
        *,
        user_id: UUID | str | None = None,
        run_id: str = "live",
        dedupe_key: str | None = None,
        priority: int | None = None,
        run_at: datetime | None = None,
        max_attempts: int | None = None,
    ) -> int | None:
        """Insert a job. With a dedupe key that already exists, nothing new is created; a still-queued
        duplicate is promoted to the higher priority / earlier run_at. Returns the job id (or None if
        the existing job had already started or finished)."""
        spec = JOB_SPECS.get(type, JobSpec(5, 3))
        params = {
            "type": type,
            "payload": Jsonb(payload or {}),
            "user_id": str(user_id) if user_id else None,
            "run_id": run_id,
            "dedupe_key": dedupe_key,
            "priority": spec.priority if priority is None else priority,
            "run_at": run_at,
            "max_attempts": max_attempts or spec.max_attempts,
        }
        q = """
            insert into jobs (type, payload, user_id, run_id, dedupe_key, priority, run_at, max_attempts)
            values (%(type)s, %(payload)s, %(user_id)s, %(run_id)s, %(dedupe_key)s, %(priority)s,
                    coalesce(%(run_at)s::timestamptz, now()), %(max_attempts)s)
            on conflict (dedupe_key) do update
              set priority = least(jobs.priority, excluded.priority),
                  run_at = least(jobs.run_at, excluded.run_at)
              where jobs.status = 'queued'
            returning id
        """
        async with self.db.system_scope() as s:
            row = await s.fetch_one(q, params)
        return int(row["id"]) if row else None

    async def claim(self, worker_id: str, types: list[str] | None = None) -> Job | None:
        q = """
            update jobs set status = 'running', locked_by = %(w)s, locked_at = now(), attempts = attempts + 1
            where id = (
              select id from jobs
              where status = 'queued' and run_at <= now()
                and (%(types)s::text[] is null or type = any(%(types)s::text[]))
              order by priority, run_at, id
              for update skip locked
              limit 1
            )
            returning *
        """
        async with self.db.system_scope() as s:
            row = await s.fetch_one(q, {"w": worker_id, "types": types})
        return Job.model_validate(row) if row else None

    async def complete(self, job_id: int) -> None:
        async with self.db.system_scope() as s:
            await s.execute(
                "update jobs set status = 'done', finished_at = now(), locked_by = null, last_error = null"
                " where id = %s",
                [job_id],
            )

    async def fail(self, job: Job, error: str, *, retryable: bool = True) -> str:
        """Record a failure: requeue with backoff, or mark failed when attempts are exhausted."""
        final = (not retryable) or job.attempts >= job.max_attempts
        async with self.db.system_scope() as s:
            if final:
                await s.execute(
                    "update jobs set status = 'failed', last_error = %s, finished_at = now(),"
                    " locked_by = null"
                    " where id = %s",
                    [error[:2000], job.id],
                )
                return "failed"
            await s.execute(
                "update jobs set status = 'queued', last_error = %s, locked_by = null, locked_at = null,"
                " run_at = now() + make_interval(secs => %s) where id = %s",
                [error[:2000], backoff_s(job.attempts), job.id],
            )
        return "queued"

    async def reap_stale(self, older_than: timedelta = timedelta(minutes=15)) -> int:
        """Requeue jobs whose worker died mid-run (or fail them if out of attempts)."""
        async with self.db.system_scope() as s:
            n = await s.execute(
                "update jobs set status = case when attempts >= max_attempts then 'failed' else 'queued' end,"
                " last_error = coalesce(last_error, '') || ' [reaped: worker lost]', locked_by = null"
                " where status = 'running' and locked_at < now() - %s",
                [older_than],
            )
        return n

    async def get(self, job_id: int) -> Job | None:
        async with self.db.system_scope() as s:
            row = await s.jobs.get(job_id)
        return Job.model_validate(row) if row else None

    async def pause_types(self, like: str) -> int:
        async with self.db.system_scope() as s:
            return await s.execute(
                "update jobs set status = 'paused' where status = 'queued' and type like %s", [like]
            )

    async def cancel_for_user(self, user_id: UUID | str) -> int:
        async with self.db.system_scope() as s:
            return await s.execute(
                "update jobs set status = 'cancelled' where status = 'queued' and user_id = %s"
                " and type <> 'delete_user'",
                [str(user_id)],
            )
