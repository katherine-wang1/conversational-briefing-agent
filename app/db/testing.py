"""Throwaway test databases: create, migrate, hand out role DSNs, drop.

Tests look for a Postgres superuser DSN in TEST_DATABASE_ADMIN_URL, then try the docker-compose
default (localhost:5432) and localhost:55432. If none answers, DB tests skip.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db.migrate import apply_migrations

CANDIDATES = (
    "postgresql://postgres:postgres@localhost:5432/postgres",
    "postgresql://postgres:postgres@localhost:55432/postgres",
)


@dataclass
class TestDatabase:
    __test__ = False  # not a pytest class

    name: str
    owner_dsn: str  # superuser / owner (migrations, fixtures that must bypass RLS)
    rw_dsn: str  # app_rw: what the app uses
    admin_dsn: str  # app_admin
    server_dsn: str

    def drop(self) -> None:
        with psycopg.connect(self.server_dsn, autocommit=True) as c:
            c.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()",
                [self.name],
            )
            c.execute(f'drop database if exists "{self.name}"')


def find_server() -> str | None:
    env = os.environ.get("TEST_DATABASE_ADMIN_URL")
    for dsn in ([env] if env else []) + list(CANDIDATES):
        try:
            with psycopg.connect(dsn, connect_timeout=2) as c:
                c.execute("select 1")
            return dsn
        except psycopg.Error:
            continue
    return None


def _with(dsn: str, **kw: str) -> str:
    d = conninfo_to_dict(dsn)
    d.update(kw)
    return make_conninfo(**d)


def create_test_database(server_dsn: str) -> TestDatabase:
    name = f"briefing_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(server_dsn, autocommit=True) as c:
        c.execute(f'create database "{name}"')
    owner = _with(server_dsn, dbname=name)
    asyncio.run(apply_migrations(owner, dev_role_passwords=True))
    return TestDatabase(
        name=name,
        owner_dsn=owner,
        rw_dsn=_with(owner, user="app_rw", password="app_rw"),
        admin_dsn=_with(owner, user="app_admin", password="app_admin"),
        server_dsn=server_dsn,
    )
