"""Worker per-type concurrency limits (no database: an in-memory queue)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.jobs.worker import Worker, default_type_limits


class MemQueue:
    def __init__(self, types: list[str]):
        self.jobs = [SimpleNamespace(id=n, type=t, attempts=1, max_attempts=3) for n, t in enumerate(types)]
        self.excludes: list[list[str] | None] = []
        self.done: list[int] = []

    async def claim(self, worker_id, types=None, exclude_types=None):
        self.excludes.append(exclude_types)
        for j in self.jobs:
            if exclude_types and j.type in exclude_types:
                continue
            if types and j.type not in types:
                continue
            self.jobs.remove(j)
            return j
        return None

    async def complete(self, job_id):
        self.done.append(job_id)

    async def fail(self, job, error, retryable=True):  # pragma: no cover - handlers don't fail here
        raise AssertionError(error)

    async def reap_stale(self):
        return 0


def test_default_limit_is_one_newsroom_run():
    assert default_type_limits() == {"newsroom_run": 1}


async def test_one_newsroom_run_at_a_time_others_unaffected():
    queue = MemQueue(["newsroom_run"] * 3 + ["cos_daily"] * 3)
    deps = SimpleNamespace(queue=queue, ports=SimpleNamespace(name="stub"))
    running = {"newsroom_run": 0, "cos_daily": 0}
    peak = dict(running)

    async def handler(deps, job):  # noqa: ANN001
        running[job.type] += 1
        peak[job.type] = max(peak[job.type], running[job.type])
        await asyncio.sleep(0.03)
        running[job.type] -= 1

    w = Worker(
        deps,
        concurrency=4,
        poll_s=0.005,
        handlers={"newsroom_run": handler, "cos_daily": handler},
        run_scheduler=False,
        type_limits={"newsroom_run": 1},
    )
    stop = asyncio.Event()
    task = asyncio.create_task(w.run_forever(stop))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(queue.done) == 6:
            break
    stop.set()
    await task
    assert sorted(queue.done) == list(range(6))
    assert peak == {"newsroom_run": 1, "cos_daily": 3}
    assert ["newsroom_run"] in queue.excludes
    assert w.running_by_type == {}
