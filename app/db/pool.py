"""Async psycopg pool and the scoped-transaction helpers (TDD §10.3).

Every database access goes through `Database.user_scope(user_id)`:

    async with db.user_scope(user_id) as s:      # a user's request or job
        rows = await s.interests.list()
    async with db.user_scope(None) as s:         # newsroom / system work: app.user_id = ''
        await s.digests.insert(...)

The scope opens one transaction and sets `app.user_id` transaction-locally before anything else,
so RLS policies apply to every statement. There is no way to get a connection without a scope.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.db.scope import Scope


class PrivilegedConnectionError(RuntimeError):
    """The app connected as a superuser or a BYPASSRLS role: isolation would not be enforced."""


def _as_uuid(user_id: UUID | str | None) -> UUID | None:
    if user_id is None or user_id == "":
        return None
    return user_id if isinstance(user_id, UUID) else UUID(str(user_id))


class Database:
    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        allow_privileged: bool = False,
        name: str = "app",
        admin: bool = False,
    ) -> None:
        self.dsn = dsn
        self.admin = admin
        self.allow_privileged = allow_privileged
        self.pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": False},
            name=name,
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=15)
        await self._check_role()

    async def close(self) -> None:
        await self.pool.close()

    async def __aenter__(self) -> Database:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _check_role(self) -> None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "select current_user as role, rolsuper, rolbypassrls from pg_roles where rolname = current_user"
            )
            row = await cur.fetchone()
            await conn.rollback()
        if row and (row["rolsuper"] or row["rolbypassrls"]) and not self.allow_privileged:
            raise PrivilegedConnectionError(
                f"connected as {row['role']!r}, which bypasses row-level security. "
                "Connect as app_rw (see docs/deploy.md)."
            )

    @asynccontextmanager
    async def user_scope(self, user_id: UUID | str | None, *, run_id: str = "live") -> AsyncIterator[Scope]:
        """One transaction acting as `user_id` (None = newsroom/system context)."""
        uid = _as_uuid(user_id)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "select set_config('app.user_id', %s, true)", [str(uid) if uid else ""]
                )
                yield Scope(conn, uid, run_id=run_id, admin=self.admin)

    def system_scope(self, *, run_id: str = "live"):  # noqa: ANN201 - async context manager
        return self.user_scope(None, run_id=run_id)
