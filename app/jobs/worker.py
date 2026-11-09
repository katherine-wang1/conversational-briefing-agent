"""Job worker: `python -m app.jobs.worker` (Railway `worker` service).

Claims jobs with FOR UPDATE SKIP LOCKED, runs up to WORKER_CONCURRENCY at once, ticks the
scheduler every minute, writes a heartbeat, and requeues jobs from dead workers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
import traceback

from app.jobs import scheduler
from app.jobs.deps import Deps
from app.jobs.handlers import HANDLERS, Handler, PermanentJobError
from app.jobs.queue import Job

log = logging.getLogger("app.jobs.worker")


class Worker:
    def __init__(
        self,
        deps: Deps,
        *,
        worker_id: str | None = None,
        concurrency: int = 4,
        poll_s: float = 1.0,
        handlers: dict[str, Handler] | None = None,
        run_scheduler: bool = True,
    ):
        self.deps = deps
        self.queue = deps.queue
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.concurrency = concurrency
        self.poll_s = poll_s
        self.handlers = handlers if handlers is not None else HANDLERS
        self.run_scheduler = run_scheduler

    async def execute(self, job: Job) -> str:
        fn = self.handlers.get(job.type)
        t0 = time.monotonic()
        if fn is None:
            return await self.queue.fail(job, f"no handler for job type {job.type!r}", retryable=False)
        try:
            await fn(self.deps, job)
        except PermanentJobError as e:
            log.warning("job %s %s failed permanently: %s", job.id, job.type, e)
            return await self.queue.fail(job, f"permanent: {e}", retryable=False)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "job %s %s failed (attempt %s/%s): %s", job.id, job.type, job.attempts, job.max_attempts, e
            )
            tb = traceback.format_exc(limit=5)
            return await self.queue.fail(job, f"{type(e).__name__}: {e}\n{tb}")
        await self.queue.complete(job.id)
        log.info("job %s %s done in %d ms", job.id, job.type, (time.monotonic() - t0) * 1000)
        return "done"

    async def run_one(self, types: list[str] | None = None) -> Job | None:
        job = await self.queue.claim(self.worker_id, types)
        if job is not None:
            await self.execute(job)
        return job

    async def drain(self, max_jobs: int = 100, types: list[str] | None = None) -> list[Job]:
        """Run queued, due jobs one by one until none are left (tests / one-off use)."""
        done = []
        for _ in range(max_jobs):
            job = await self.run_one(types)
            if job is None:
                break
            done.append(job)
        return done

    async def heartbeat(self) -> None:
        async with self.deps.db.system_scope() as s:
            await s.worker_heartbeats.upsert(
                worker_id=self.worker_id,
                seen_at=self.deps.clock(),
                info={"concurrency": self.concurrency, "agents": self.deps.ports.name},
            )

    async def run_forever(self, stop: asyncio.Event) -> None:
        running: set[asyncio.Task] = set()
        last_tick = last_reap = 0.0
        log.info(
            "worker %s started (concurrency %s, agents=%s)",
            self.worker_id,
            self.concurrency,
            self.deps.ports.name,
        )
        while not stop.is_set():
            now = time.monotonic()
            try:
                if self.run_scheduler and now - last_tick >= 60:
                    last_tick = now  # set first: a failing tick must not block job claiming
                    await scheduler.tick(self.deps.db, self.queue, self.deps.clock())
                    await self.heartbeat()
                if now - last_reap >= 300:
                    last_reap = now
                    await self.queue.reap_stale()
                claimed = False
                while len(running) < self.concurrency and not stop.is_set():
                    job = await self.queue.claim(self.worker_id)
                    if job is None:
                        break
                    claimed = True
                    task = asyncio.create_task(self.execute(job))
                    running.add(task)
                    task.add_done_callback(running.discard)
            except Exception:  # noqa: BLE001 - keep the loop alive across DB hiccups
                log.exception("worker loop error")
                claimed = False
            if not claimed:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_s)
                except TimeoutError:
                    pass
        if running:
            await asyncio.gather(*running, return_exceptions=True)


async def main() -> None:
    from app.config.env_file import load_env_file
    from app.config.settings import get_settings

    load_env_file()  # before anything reads os.environ (model layer, speech, fastembed threads)
    from app.db.pool import Database
    from app.jobs.deps import build_deps

    logging.basicConfig(
        level=logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    s = get_settings()
    db = Database(s.database_url, min_size=1, max_size=s.worker_concurrency + 2, name="worker")
    await db.open()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    deps = None
    try:
        deps = build_deps(db, s)
        worker = Worker(deps, concurrency=s.worker_concurrency, poll_s=s.worker_poll_s)
        await worker.run_forever(stop)
    finally:
        if deps is not None:
            from app.llm.factory import flush_ledger

            await flush_ledger(deps.model_client)
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
