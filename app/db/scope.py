"""A scoped transaction: the connection plus the acting user. Repositories hang off it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from psycopg import AsyncConnection

if TYPE_CHECKING:
    from app.db import repos as r


class ScopeError(RuntimeError):
    """A per-user write was attempted from a system scope (or vice versa)."""


class Scope:
    def __init__(
        self,
        conn: AsyncConnection[dict[str, Any]],
        user_id: UUID | None,
        *,
        run_id: str = "live",
        admin: bool = False,
    ):
        self.conn = conn
        self.user_id = user_id
        self.run_id = run_id
        self.admin = admin  # connected as app_admin: read-only views granted by policy
        self._repos: dict[str, Any] = {}

    @property
    def is_system(self) -> bool:
        return self.user_id is None

    def require_user(self) -> UUID:
        if self.user_id is None:
            raise ScopeError("this operation needs a user scope")
        return self.user_id

    # ---- raw SQL (still inside the scoped transaction, so RLS applies) ------------------------------

    async def fetch_all(self, query: Any, params: Any = None) -> list[dict[str, Any]]:
        cur = await self.conn.execute(query, params)
        return await cur.fetchall()

    async def fetch_one(self, query: Any, params: Any = None) -> dict[str, Any] | None:
        cur = await self.conn.execute(query, params)
        return await cur.fetchone()

    async def execute(self, query: Any, params: Any = None) -> int:
        cur = await self.conn.execute(query, params)
        return cur.rowcount

    # ---- repositories ---------------------------------------------------------------------------------

    def repo(self, table: str) -> Any:
        from app.db.repos import REPOS

        if table not in self._repos:
            self._repos[table] = REPOS[table](self)
        return self._repos[table]

    def __getattr__(self, name: str) -> Any:
        from app.db.repos import REPOS

        if name in REPOS:
            return self.repo(name)
        raise AttributeError(name)

    if TYPE_CHECKING:  # explicit types for editors / mypy
        profiles: r.ProfilesRepo
        interests: r.InterestsRepo
        delivery_prefs: r.DeliveryPrefsRepo
        memory_snapshots: r.MemorySnapshotsRepo
        memory_signals: r.MemorySignalsRepo
        briefs: r.BriefsRepo
        candidates: r.CandidatesRepo
        source_packs: r.SourcePacksRepo
        sessions: r.SessionsRepo
        turns: r.TurnsRepo
        open_questions: r.OpenQuestionsRepo
        saved_items: r.SavedItemsRepo
        ratings: r.RatingsRepo
        experiment_assignments: r.ExperimentAssignmentsRepo
        topic_requests: r.TopicRequestsRepo
        llm_calls: r.LlmCallsRepo
        headline_observations: r.HeadlineObservationsRepo
        documents: r.DocumentsRepo
        passages: r.PassagesRepo
        stories: r.StoriesRepo
        story_items: r.StoryItemsRepo
        newsroom_runs: r.NewsroomRunsRepo
        digests: r.DigestsRepo
        digest_entries: r.DigestEntriesRepo
        jobs: r.JobsRepo
        invites: r.InvitesRepo
        admin_audit: r.AdminAuditRepo
        worker_heartbeats: r.WorkerHeartbeatsRepo
        admin_alerts: r.AdminAlertsRepo
