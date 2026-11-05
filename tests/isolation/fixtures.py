"""Postgres fixtures shared by tests/isolation and tests/unit/platform (imported by their conftests)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from app.db.pool import Database
from app.db.testing import TestDatabase, create_test_database, find_server


@pytest.fixture(scope="session")
def pgdb() -> Iterator[TestDatabase]:
    server = find_server()
    if server is None:
        pytest.skip("Postgres unavailable (set TEST_DATABASE_ADMIN_URL or run docker compose up db)")
    tdb = create_test_database(server)
    try:
        yield tdb
    finally:
        tdb.drop()


@pytest_asyncio.fixture
async def db(pgdb: TestDatabase) -> AsyncIterator[Database]:
    d = Database(pgdb.rw_dsn, min_size=1, max_size=4)
    await d.open()
    try:
        yield d
    finally:
        await d.close()


@pytest_asyncio.fixture
async def admin_db(pgdb: TestDatabase) -> AsyncIterator[Database]:
    d = Database(pgdb.admin_dsn, min_size=1, max_size=2, admin=True, name="admin")
    await d.open()
    try:
        yield d
    finally:
        await d.close()


async def make_user(db: Database, *, email: str | None = None, role: str = "user", **kw: object) -> uuid.UUID:
    uid = uuid.uuid4()
    async with db.user_scope(uid) as s:
        await s.profiles.insert(
            user_id=uid, email=email or f"{uid.hex[:8]}@example.test", role=role, status="active", **kw
        )
    return uid
