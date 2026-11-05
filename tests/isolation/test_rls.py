"""Isolation suite (TDD §10.3, §24): RLS on every per-user table, as role app_rw.

For every per-user table:
- user A can't read, update, delete, or insert user B's rows (and can't move own rows to B);
- a newsroom context (app.user_id = '') sees zero per-user rows and can't write them.
Plus: llm_calls is append-only for users, system tables are invisible to users, app_rw is not
privileged, and app_admin only reads what §10.3 grants.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import psycopg
import pytest
from psycopg import sql

from app.db.pool import Database, PrivilegedConnectionError
from app.db.repos import USER_TABLES
from app.db.scope import ScopeError
from tests.isolation.fixtures import make_user


def _ids() -> str:
    return uuid.uuid4().hex[:12]


async def seed_user_rows(db: Database, uid: uuid.UUID) -> dict[str, dict[str, Any]]:
    """One row in every per-user table for `uid`, inserted in uid's own scope."""
    out: dict[str, dict[str, Any]] = {}
    async with db.user_scope(uid) as s:
        out["profiles"] = await s.profiles.me()
        brief = await s.briefs.insert(id="b" + _ids(), local_date=date(2026, 11, 5), script={"x": 1})
        out["briefs"] = brief
        sess = await s.sessions.insert(id="s" + _ids(), brief_id=brief["id"])
        out["sessions"] = sess
        out["interests"] = await s.interests.insert(
            label="agent memory", subtopic="agents-and-tools", weight=0.4, confidence=0.3, source="onboarding"
        )
        out["delivery_prefs"] = await s.delivery_prefs.set("length_s", 300, 0.5)
        out["memory_snapshots"] = await s.memory_snapshots.insert(as_of_date=date(2026, 11, 5), payload={})
        out["memory_signals"] = await s.memory_signals.insert(session_id=sess["id"], type="dive")
        out["candidates"] = await s.candidates.insert(brief_id=brief["id"], story_ref="st1", origin="digest")
        out["source_packs"] = await s.source_packs.insert(brief_id=brief["id"], story_id="st1", pack={})
        out["turns"] = await s.turns.insert(id="t" + _ids(), session_id=sess["id"], role="user", text="hi")
        out["open_questions"] = await s.open_questions.insert(id="q" + _ids(), question="who funded it?")
        out["saved_items"] = await s.saved_items.insert(story_id="st1")
        out["ratings"] = await s.ratings.insert(id="r" + _ids(), usefulness=4)
        out["experiment_assignments"] = await s.experiment_assignments.insert(
            local_date=date(2026, 11, 5), arm="full"
        )
        out["topic_requests"] = await s.topic_requests.insert(text="more on batteries")
    assert set(out) == set(USER_TABLES)
    return out


def _new_row_for(table: str, owner: uuid.UUID, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Minimal valid new row for `table` claiming to belong to `owner` (FK parents from `rows`)."""
    b, s = rows["briefs"]["id"], rows["sessions"]["id"]
    base: dict[str, dict[str, Any]] = {
        "profiles": {"user_id": owner},  # pk clash is irrelevant: RLS rejects before
        "interests": {"label": "x", "subtopic": "other", "source": "explicit"},
        "delivery_prefs": {"key": "depth", "value": psycopg.types.json.Jsonb("deep")},
        "memory_snapshots": {"as_of_date": date(2026, 1, 1), "payload": psycopg.types.json.Jsonb({})},
        "memory_signals": {"type": "skip"},
        "briefs": {"id": "b" + _ids(), "local_date": date(2026, 1, 2)},
        "candidates": {"brief_id": b, "story_ref": "z", "origin": "digest"},
        "source_packs": {"brief_id": b, "story_id": "z" + _ids(), "pack": psycopg.types.json.Jsonb({})},
        "sessions": {"id": "s" + _ids()},
        "turns": {"id": "t" + _ids(), "session_id": s, "role": "user"},
        "open_questions": {"id": "q" + _ids(), "question": "?"},
        "saved_items": {"story_id": "z" + _ids()},
        "ratings": {"id": "r" + _ids()},
        "experiment_assignments": {"local_date": date(2026, 1, 3), "arm": "full"},
        "topic_requests": {"text": "x"},
    }
    row = dict(base[table])
    row["user_id"] = owner
    return row


async def _raw_insert(scope: Any, table: str, row: dict[str, Any]) -> None:
    q = sql.SQL("insert into {} ({}) values ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, row)),
        sql.SQL(", ").join(sql.Placeholder() * len(row)),
    )
    await scope.execute(q, list(row.values()))


@pytest.fixture
async def two_users(db: Database):
    a = await make_user(db)
    b = await make_user(db)
    rows_a = await seed_user_rows(db, a)
    rows_b = await seed_user_rows(db, b)
    return a, b, rows_a, rows_b


@pytest.mark.parametrize("table", USER_TABLES)
async def test_user_cannot_read_other_users_rows(db: Database, two_users, table: str):
    a, b, _, _ = two_users
    async with db.user_scope(a) as s:
        rows = await s.fetch_all(sql.SQL("select user_id from {}").format(sql.Identifier(table)))
        assert rows, f"user A should see own {table} rows"
        assert {r["user_id"] for r in rows} == {a}
        n = await s.fetch_one(
            sql.SQL("select count(*) as n from {} where user_id = %s").format(sql.Identifier(table)), [b]
        )
        assert n["n"] == 0


@pytest.mark.parametrize("table", USER_TABLES)
async def test_user_cannot_update_or_delete_other_users_rows(db: Database, two_users, table: str):
    a, b, _, _ = two_users
    async with db.user_scope(a) as s:
        upd = await s.execute(
            sql.SQL("update {} set run_id = run_id where user_id = %s").format(sql.Identifier(table)), [b]
        )
        assert upd == 0
        dele = await s.execute(
            sql.SQL("delete from {} where user_id = %s").format(sql.Identifier(table)), [b]
        )
        assert dele == 0
    async with db.user_scope(b) as s:
        n = await s.fetch_one(sql.SQL("select count(*) as n from {}").format(sql.Identifier(table)))
        assert n["n"] >= 1, f"user B's {table} rows must survive user A's update/delete"


@pytest.mark.parametrize("table", USER_TABLES)
async def test_user_cannot_insert_rows_for_other_user(db: Database, two_users, table: str):
    a, b, rows_a, _ = two_users
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with db.user_scope(a) as s:
            await _raw_insert(s, table, _new_row_for(table, b, rows_a))


@pytest.mark.parametrize("table", USER_TABLES)
async def test_user_cannot_hand_own_rows_to_other_user(db: Database, two_users, table: str):
    a, b, _, _ = two_users
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with db.user_scope(a) as s:
            await s.execute(
                sql.SQL("update {} set user_id = %s where user_id = %s").format(sql.Identifier(table)), [b, a]
            )


@pytest.mark.parametrize("table", USER_TABLES)
async def test_newsroom_context_sees_no_user_rows(db: Database, two_users, table: str):
    async with db.user_scope(None) as s:
        n = await s.fetch_one(sql.SQL("select count(*) as n from {}").format(sql.Identifier(table)))
        assert n["n"] == 0
        upd = await s.execute(sql.SQL("update {} set run_id = run_id").format(sql.Identifier(table)))
        assert upd == 0
        dele = await s.execute(sql.SQL("delete from {}").format(sql.Identifier(table)))
        assert dele == 0


@pytest.mark.parametrize("table", USER_TABLES)
async def test_newsroom_context_cannot_write_user_rows(db: Database, two_users, table: str):
    a, _, rows_a, _ = two_users
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with db.user_scope(None) as s:
            await _raw_insert(s, table, _new_row_for(table, a, rows_a))


async def test_repositories_refuse_per_user_access_from_system_scope(db: Database, two_users):
    async with db.user_scope(None) as s:
        with pytest.raises(ScopeError):
            await s.interests.list()
        with pytest.raises(ScopeError):
            await s.briefs.insert(id="x", local_date=date(2026, 1, 1))


async def test_system_tables_invisible_to_users(db: Database, two_users):
    a, _, _, _ = two_users
    async with db.user_scope(None) as s:
        await s.jobs.insert(type="cleanup", dedupe_key="iso:" + _ids())
        await s.invites.insert(email=f"{_ids()}@example.test")
    async with db.user_scope(a) as s:
        for t in ("jobs", "invites", "admin_audit", "worker_heartbeats", "admin_alerts"):
            n = await s.fetch_one(sql.SQL("select count(*) as n from {}").format(sql.Identifier(t)))
            assert n["n"] == 0, t
        with pytest.raises(ScopeError):
            await s.jobs.list()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with db.user_scope(a) as s:
            await s.execute("insert into jobs (type) values ('cleanup')")


async def test_llm_calls_append_only_for_users(db: Database, two_users):
    a, _, _, _ = two_users
    async with db.user_scope(a) as s:
        await s.llm_calls.insert(user_id=a, role="voice.turn", provider="fake", model="m", cost_usd=0.01)
        n = await s.fetch_one("select count(*) as n from llm_calls")
        assert n["n"] == 0  # users can't read the ledger, even their own rows
    async with db.user_scope(None) as s:
        n = await s.fetch_one("select count(*) as n from llm_calls where user_id = %s", [a])
        assert n["n"] == 1


async def test_shared_tables_usable_from_newsroom_and_user_contexts(db: Database, two_users):
    a, _, _, _ = two_users
    did = "d" + _ids()
    async with db.user_scope(None) as s:
        await s.digests.insert(id=did, beat="ai", at="2026-11-05T06:00:00Z", version="v1")
        await s.digest_entries.insert(digest_id=did, story_id="st1", headline="Acme ships X", importance=70)
    async with db.user_scope(a) as s:
        assert await s.digests.get(did) is not None


async def test_app_rw_is_not_privileged(pgdb, db: Database):
    async with db.user_scope(None) as s:
        r = await s.fetch_one("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")
        assert r == {"rolsuper": False, "rolbypassrls": False}
    owner = Database(pgdb.owner_dsn, min_size=1, max_size=1)
    with pytest.raises(PrivilegedConnectionError):
        await owner.open()
    await owner.close()


async def test_app_admin_reads_only_granted_tables(db: Database, admin_db: Database, two_users):
    a, _, _, _ = two_users
    async with admin_db.user_scope(None) as s:
        assert await s.profiles.get(a) is not None
        await s.fetch_all("select * from jobs")
        await s.fetch_all("select * from llm_calls")
    for t in ("interests", "briefs", "turns", "sessions", "memory_signals"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with admin_db.user_scope(None) as s:
                await s.fetch_all(sql.SQL("select * from {}").format(sql.Identifier(t)))


async def test_scope_setting_is_transaction_local(db: Database, two_users):
    a, _, _, _ = two_users
    async with db.user_scope(a) as s:
        r = await s.fetch_one("select current_setting('app.user_id', true) as u")
        assert r["u"] == str(a)
    # the pool may hand back the same connection: the setting must not leak out of the transaction
    for _ in range(3):
        async with db.user_scope(None) as s:
            r = await s.fetch_one("select current_setting('app.user_id', true) as u")
            assert r["u"] == ""
            n = await s.fetch_one("select count(*) as n from profiles")
            assert n["n"] == 0


async def test_account_deletion_cascades_only_own_rows(db: Database, two_users):
    a, b, _, _ = two_users
    async with db.user_scope(a) as s:
        await s.profiles.delete(a)
    async with db.user_scope(a) as s:
        for t in USER_TABLES:
            n = await s.fetch_one(sql.SQL("select count(*) as n from {}").format(sql.Identifier(t)))
            assert n["n"] == 0, t
    async with db.user_scope(b) as s:
        for t in USER_TABLES:
            n = await s.fetch_one(sql.SQL("select count(*) as n from {}").format(sql.Identifier(t)))
            assert n["n"] >= 1, t
