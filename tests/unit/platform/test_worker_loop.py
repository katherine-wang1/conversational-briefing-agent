from __future__ import annotations

import asyncio

from app.db.pool import Database
from app.jobs.worker import Worker
from tests.unit.platform.test_jobs import make_deps


async def test_run_forever_ticks_heartbeats_and_runs_jobs(db: Database, tmp_path):
    async with db.system_scope() as s:
        await s.execute("delete from jobs")
    deps = make_deps(db, tmp_path)
    ran = []

    async def ok(deps, job):  # noqa: ANN001
        ran.append(job.type)

    handlers = {t: ok for t in ("cleanup", "spend_guard", "newsroom_run", "cos_daily", "daily_snapshot")}
    w = Worker(deps, worker_id="wtest", handlers=handlers, poll_s=0.02)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run_forever(stop))
    for _ in range(200):
        await asyncio.sleep(0.02)
        if {"cleanup", "spend_guard"} <= set(ran):
            break
    stop.set()
    await task
    assert {"cleanup", "spend_guard"} <= set(ran)  # enqueued by the first scheduler tick, then run
    async with db.system_scope() as s:
        hb = await s.worker_heartbeats.get("wtest")
    assert hb["info"]["concurrency"] == 4 and hb["info"]["agents"] == "stub"
