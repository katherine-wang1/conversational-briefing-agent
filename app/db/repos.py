"""One repository per table (TDD §3.1: hand-written repository layer, no ORM).

Repositories are only reachable from a `Scope` (see app/db/pool.py), so every statement runs in a
transaction with `app.user_id` set. Per-user repositories fill `user_id` from the scope and refuse
to run in a system scope; RLS enforces the same thing underneath.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any, ClassVar

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.db.scope import Scope, ScopeError


def _jsonable(v: Any) -> Any:
    if isinstance(v, BaseModel):
        return v.model_dump(mode="json")
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


class Repo:
    table: ClassVar[str]
    pk: ClassVar[tuple[str, ...]] = ("id",)
    json_cols: ClassVar[frozenset[str]] = frozenset()
    per_user: ClassVar[bool] = False
    has_run_id: ClassVar[bool] = True
    default_order: ClassVar[str | None] = None

    def __init__(self, scope: Scope):
        self.s = scope

    # ---- helpers ------------------------------------------------------------------------------------

    def _prep(self, values: Mapping[str, Any], *, for_insert: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in values.items():
            out[k] = Jsonb(_jsonable(v)) if k in self.json_cols and v is not None else v
        if self.per_user:
            uid = self.s.require_user()
            if for_insert:
                out.setdefault("user_id", uid)
        if for_insert and self.has_run_id:
            out.setdefault("run_id", self.s.run_id)
        return out

    def _where(self, where: Mapping[str, Any]) -> tuple[sql.Composable, list[Any]]:
        parts: list[sql.Composable] = []
        params: list[Any] = []
        for k, v in where.items():
            if v is None:
                parts.append(sql.SQL("{} is null").format(sql.Identifier(k)))
            elif isinstance(v, list | tuple | set):
                parts.append(sql.SQL("{} = any(%s)").format(sql.Identifier(k)))
                params.append(list(v))
            else:
                parts.append(sql.SQL("{} = %s").format(sql.Identifier(k)))
                params.append(v)
        if not parts:
            return sql.SQL(""), params
        return sql.SQL(" where ") + sql.SQL(" and ").join(parts), params

    def _check_scope(self) -> None:
        if self.per_user and not self.s.admin:
            self.s.require_user()

    # ---- CRUD ---------------------------------------------------------------------------------------

    async def insert(self, **values: Any) -> dict[str, Any]:
        vals = self._prep(values, for_insert=True)
        q = sql.SQL("insert into {} ({}) values ({}) returning *").format(
            sql.Identifier(self.table),
            sql.SQL(", ").join(map(sql.Identifier, vals)),
            sql.SQL(", ").join(sql.Placeholder() * len(vals)),
        )
        row = await self.s.fetch_one(q, list(vals.values()))
        assert row is not None
        return row

    async def upsert(self, conflict: Iterable[str] | None = None, **values: Any) -> dict[str, Any]:
        vals = self._prep(values, for_insert=True)
        keys = tuple(conflict or self.pk)
        updates = [k for k in vals if k not in keys]
        action = (
            sql.SQL("do update set ")
            + sql.SQL(", ").join(
                sql.SQL("{} = excluded.{}").format(sql.Identifier(k), sql.Identifier(k)) for k in updates
            )
            if updates
            else sql.SQL("do nothing")
        )
        q = sql.SQL("insert into {} ({}) values ({}) on conflict ({}) {} returning *").format(
            sql.Identifier(self.table),
            sql.SQL(", ").join(map(sql.Identifier, vals)),
            sql.SQL(", ").join(sql.Placeholder() * len(vals)),
            sql.SQL(", ").join(map(sql.Identifier, keys)),
            action,
        )
        row = await self.s.fetch_one(q, list(vals.values()))
        if row is None:  # do nothing on conflict
            row = await self.find_one(**{k: vals[k] for k in keys})
        assert row is not None
        return row

    async def get(self, *pk: Any) -> dict[str, Any] | None:
        self._check_scope()
        return await self.find_one(**dict(zip(self.pk, pk, strict=True)))

    async def find_one(self, **where: Any) -> dict[str, Any] | None:
        rows = await self.list(limit=1, **where)
        return rows[0] if rows else None

    async def list(
        self, *, order_by: str | None = None, desc: bool = False, limit: int | None = None, **where: Any
    ) -> list[dict[str, Any]]:
        self._check_scope()
        w, params = self._where(where)
        q = sql.SQL("select * from {}").format(sql.Identifier(self.table)) + w
        order = order_by or self.default_order
        if order:
            q += sql.SQL(" order by {} {}").format(sql.Identifier(order), sql.SQL("desc" if desc else "asc"))
        if limit is not None:
            q += sql.SQL(" limit {}").format(sql.Literal(int(limit)))
        return await self.s.fetch_all(q, params)

    async def count(self, **where: Any) -> int:
        self._check_scope()
        w, params = self._where(where)
        q = sql.SQL("select count(*) as n from {}").format(sql.Identifier(self.table)) + w
        row = await self.s.fetch_one(q, params)
        return int(row["n"]) if row else 0

    async def update_where(self, where: Mapping[str, Any], **values: Any) -> list[dict[str, Any]]:
        self._check_scope()
        vals = self._prep(values, for_insert=False)
        w, wparams = self._where(where)
        q = (
            sql.SQL("update {} set ").format(sql.Identifier(self.table))
            + sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in vals)
            + w
            + sql.SQL(" returning *")
        )
        return await self.s.fetch_all(q, [*vals.values(), *wparams])

    async def update(self, *pk: Any, **values: Any) -> dict[str, Any] | None:
        rows = await self.update_where(dict(zip(self.pk, pk, strict=True)), **values)
        return rows[0] if rows else None

    async def delete_where(self, **where: Any) -> int:
        self._check_scope()
        if not where and self.per_user:
            raise ScopeError("refusing an unfiltered delete; pass explicit filters")
        w, params = self._where(where)
        return await self.s.execute(sql.SQL("delete from {}").format(sql.Identifier(self.table)) + w, params)

    async def delete(self, *pk: Any) -> int:
        return await self.delete_where(**dict(zip(self.pk, pk, strict=True)))


# ---- per-user tables ----------------------------------------------------------------------------------


class UserRepo(Repo):
    per_user = True


class ProfilesRepo(UserRepo):
    table = "profiles"
    pk = ("user_id",)
    json_cols = frozenset({"settings"})

    async def me(self) -> dict[str, Any] | None:
        return await self.get(self.s.require_user())


class InterestsRepo(UserRepo):
    table = "interests"
    json_cols = frozenset({"evidence"})
    default_order = "created_at"


class DeliveryPrefsRepo(UserRepo):
    table = "delivery_prefs"
    pk = ("user_id", "run_id", "key")
    json_cols = frozenset({"value"})

    async def set(self, key: str, value: Any, confidence: float) -> dict[str, Any]:
        return await self.upsert(
            key=key, value=value, confidence=confidence, updated_at=datetime.now().astimezone()
        )


class MemorySnapshotsRepo(UserRepo):
    table = "memory_snapshots"
    json_cols = frozenset({"payload"})
    default_order = "as_of_date"


class MemorySignalsRepo(UserRepo):
    table = "memory_signals"
    json_cols = frozenset({"payload", "session_ctx"})
    default_order = "created_at"


class BriefsRepo(UserRepo):
    table = "briefs"
    json_cols = frozenset({"script", "selection", "audio_manifest"})
    default_order = "local_date"


class CandidatesRepo(UserRepo):
    table = "candidates"
    json_cols = frozenset({"scores"})


class SourcePacksRepo(UserRepo):
    table = "source_packs"
    pk = ("brief_id", "story_id")
    json_cols = frozenset({"pack"})


class SessionsRepo(UserRepo):
    table = "sessions"
    json_cols = frozenset({"client", "position", "memory_outcome"})
    default_order = "started_at"


class TurnsRepo(UserRepo):
    table = "turns"
    json_cols = frozenset({"citations", "playback_pos", "timings"})
    default_order = "seq"


class OpenQuestionsRepo(UserRepo):
    table = "open_questions"
    json_cols = frozenset({"context", "answer", "citations"})
    default_order = "created_at"


class SavedItemsRepo(UserRepo):
    table = "saved_items"
    default_order = "created_at"


class RatingsRepo(UserRepo):
    table = "ratings"
    json_cols = frozenset({"per_story"})
    default_order = "created_at"


class ExperimentAssignmentsRepo(UserRepo):
    table = "experiment_assignments"
    pk = ("user_id", "run_id", "local_date")


class TopicRequestsRepo(UserRepo):
    table = "topic_requests"
    default_order = "created_at"


class LlmCallsRepo(Repo):
    """Append from any scope; readable only in a system scope (RLS)."""

    table = "llm_calls"

    async def insert(self, **values: Any) -> dict[str, Any]:
        # No RETURNING: in a user scope the row is not readable back (users can't read the ledger).
        vals = self._prep(values, for_insert=True)
        q = sql.SQL("insert into llm_calls ({}) values ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, vals)),
            sql.SQL(", ").join(sql.Placeholder() * len(vals)),
        )
        await self.s.execute(q, list(vals.values()))
        return {}

    async def spend_by_provider(self, since: datetime | None = None) -> dict[str, float]:
        rows = await self.s.fetch_all(
            "select provider, coalesce(sum(cost_usd), 0)::float as usd from llm_calls"
            " where run_id = 'live' and (%s::timestamptz is null or created_at >= %s) group by provider",
            [since, since],
        )
        return {r["provider"]: float(r["usd"]) for r in rows}


USER_TABLES: tuple[str, ...] = (
    "profiles",
    "interests",
    "delivery_prefs",
    "memory_snapshots",
    "memory_signals",
    "briefs",
    "candidates",
    "source_packs",
    "sessions",
    "turns",
    "open_questions",
    "saved_items",
    "ratings",
    "experiment_assignments",
    "topic_requests",
)


# ---- shared tables ------------------------------------------------------------------------------------


class HeadlineObservationsRepo(Repo):
    table = "headline_observations"
    json_cols = frozenset({"engagement", "extra"})
    default_order = "observed_at"


class DocumentsRepo(Repo):
    table = "documents"
    pk = ("url_hash",)
    has_run_id = False


class PassagesRepo(Repo):
    table = "passages"
    has_run_id = False


class StoriesRepo(Repo):
    table = "stories"
    json_cols = frozenset({"item_urls", "features"})


class StoryItemsRepo(Repo):
    table = "story_items"
    has_run_id = False


class NewsroomRunsRepo(Repo):
    table = "newsroom_runs"


class DigestsRepo(Repo):
    table = "digests"
    default_order = "at"

    async def latest(
        self, beat: str, at: datetime, run_id: str = "live", *, real_only: bool = False
    ) -> dict[str, Any] | None:
        """Latest digest at or before `at`. `real_only` skips StubAgents placeholders (version 'stub')."""
        stub = " and version <> 'stub'" if real_only else ""
        return await self.s.fetch_one(
            f"select * from digests where beat = %s and run_id = %s and at <= %s{stub}"
            " order by at desc limit 1",
            [beat, run_id, at],
        )


class DigestEntriesRepo(Repo):
    table = "digest_entries"
    pk = ("digest_id", "story_id")
    json_cols = frozenset({"evidence", "features", "urls"})
    has_run_id = False


# ---- system tables --------------------------------------------------------------------------------------


class SystemRepo(Repo):
    def _check_scope(self) -> None:
        if not self.s.is_system:
            raise ScopeError(f"{self.table} is only accessible from a system scope")


class JobsRepo(SystemRepo):
    table = "jobs"
    json_cols = frozenset({"payload"})
    default_order = "id"


class InvitesRepo(SystemRepo):
    table = "invites"
    pk = ("email",)
    has_run_id = False


class AdminAuditRepo(SystemRepo):
    table = "admin_audit"
    json_cols = frozenset({"detail"})
    has_run_id = False


class WorkerHeartbeatsRepo(SystemRepo):
    table = "worker_heartbeats"
    pk = ("worker_id",)
    json_cols = frozenset({"info"})
    has_run_id = False


class AdminAlertsRepo(SystemRepo):
    table = "admin_alerts"
    has_run_id = False


REPOS: dict[str, type[Repo]] = {
    cls.table: cls
    for cls in (
        ProfilesRepo,
        InterestsRepo,
        DeliveryPrefsRepo,
        MemorySnapshotsRepo,
        MemorySignalsRepo,
        BriefsRepo,
        CandidatesRepo,
        SourcePacksRepo,
        SessionsRepo,
        TurnsRepo,
        OpenQuestionsRepo,
        SavedItemsRepo,
        RatingsRepo,
        ExperimentAssignmentsRepo,
        TopicRequestsRepo,
        LlmCallsRepo,
        HeadlineObservationsRepo,
        DocumentsRepo,
        PassagesRepo,
        StoriesRepo,
        StoryItemsRepo,
        NewsroomRunsRepo,
        DigestsRepo,
        DigestEntriesRepo,
        JobsRepo,
        InvitesRepo,
        AdminAuditRepo,
        WorkerHeartbeatsRepo,
        AdminAlertsRepo,
    )
}
