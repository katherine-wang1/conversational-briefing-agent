"""newsroom_run N1: concurrent source collection in config order; batched observation inserts (no DB)."""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

import pytest

from app.db.repos import HeadlineObservationsRepo
from app.db.scope import Scope
from app.jobs.handlers import collect_observations


def headline_tool(seed: int, *, errors=(), raises=()):
    rng = random.Random(seed)

    async def tool(ctx, *, source_id, at):  # noqa: ANN001
        await asyncio.sleep(rng.uniform(0, 0.01))
        if source_id in raises:
            raise RuntimeError("adapter crashed")
        if source_id in errors:
            return SimpleNamespace(ok=False, error="unavailable", data=None)
        return SimpleNamespace(
            ok=True, data=SimpleNamespace(observations=[f"{source_id}:1", f"{source_id}:2"])
        )

    return tool


SOURCES = [SimpleNamespace(id=f"src{n}") for n in range(8)]


async def test_sources_collected_concurrently_in_config_order(caplog):
    deps = SimpleNamespace(tools={"headline_source": headline_tool(1, errors={"src3"})})
    obs = await collect_observations(deps, None, "ai", SOURCES, at=None)
    assert obs == [f"src{n}:{k}" for n in range(8) if n != 3 for k in (1, 2)]
    assert "src3 skipped" in caplog.text


async def test_a_raising_source_still_fails_the_job():
    deps = SimpleNamespace(tools={"headline_source": headline_tool(2, raises={"src5"})})
    with pytest.raises(RuntimeError):
        await collect_observations(deps, None, "ai", SOURCES, at=None)


class FakeCursor:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def executemany(self, query, params):
        self.log.append(("executemany", query, params))


class FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)


async def test_insert_many_is_one_executemany():
    conn = FakeConn()
    repo = HeadlineObservationsRepo(Scope(conn, None))
    rows = [{"id": f"ob{n}", "source_id": "s", "engagement": {"hn_points": n}} for n in range(3)]
    assert await repo.insert_many(rows) == 3
    assert await repo.insert_many([]) == 0
    assert len(conn.log) == 1
    _, query, params = conn.log[0]
    assert len(params) == 3 and params[0][0] == "ob0" and params[0][-1] == "live"  # run_id filled in
    assert params[2][2].obj == {"hn_points": 2}  # json columns wrapped
