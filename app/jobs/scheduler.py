"""Scheduler tick (TDD §20), run about once a minute by every worker. Idempotent: dedupe keys make
repeated ticks (and several workers ticking at once) enqueue each job exactly once.

- per user: `cos_daily` at brief_time − cos_lead_min in the user's timezone; `daily_snapshot` at 23:55
- per beat: `newsroom_run` at each `refresh_live_utc` slot (only the latest slot passed, within 3 h);
  a beat with no digest in the last 24 h (a fresh deploy) gets one for its latest slot at once
- `spend_guard` hourly, `cleanup` daily
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import loader
from app.db.pool import Database
from app.jobs.queue import JobQueue

log = logging.getLogger(__name__)

SNAPSHOT_AT = time(23, 55)
NEWSROOM_GRACE = timedelta(hours=3)
COS_GRACE = timedelta(hours=12)
BOOTSTRAP_AFTER = timedelta(hours=24)


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def cos_due(now: datetime, tz_name: str, brief_time: time, lead_min: int) -> date | None:
    """The local brief date whose cos_daily should be enqueued now, if any."""
    tz = _tz(tz_name)
    local = now.astimezone(tz)
    for d in (local.date(), local.date() + timedelta(days=1)):
        brief_at = datetime.combine(d, brief_time, tz)
        start = brief_at - timedelta(minutes=lead_min)
        if start <= local < brief_at + COS_GRACE:
            return d
    return None


def latest_slot(
    now: datetime, slots_utc: list[str], grace: timedelta | None = NEWSROOM_GRACE
) -> datetime | None:
    now = now.astimezone(UTC)
    best = None
    for day in (now.date() - timedelta(days=1), now.date()):
        for hhmm in slots_utc:
            h, m = (int(x) for x in hhmm.split(":"))
            t = datetime.combine(day, time(h, m), UTC)
            if t <= now and (best is None or t > best):
                best = t
    if best is not None and (grace is None or now - best <= grace):
        return best
    return None


async def tick(db: Database, queue: JobQueue, now: datetime | None = None) -> list[int]:
    now = now or datetime.now(UTC)
    lead = int((loader.agent().get("schedule") or {}).get("cos_lead_min", 60))
    ids: list[int | None] = []

    async with db.system_scope() as s:
        targets = await s.fetch_all("select * from app_schedule_targets()")
    for t in targets:
        uid = t["user_id"]
        d = cos_due(now, t["timezone"], t["brief_time"], lead)
        if d is not None:
            ids.append(
                await queue.enqueue(
                    "cos_daily", {"local_date": d.isoformat()}, user_id=uid, dedupe_key=f"cos:{uid}:{d}"
                )
            )
        local = now.astimezone(_tz(t["timezone"]))
        if local.time() >= SNAPSHOT_AT:
            ids.append(
                await queue.enqueue(
                    "daily_snapshot",
                    {"local_date": local.date().isoformat()},
                    user_id=uid,
                    dedupe_key=f"snap:{uid}:{local.date()}",
                )
            )

    for beat in loader.beats().values():
        slot = latest_slot(now, beat.refresh_live_utc)
        async with db.system_scope() as s:
            # Placeholder digests (StubAgents, e.g. a worker started without a model key) don't count.
            recent = await s.digests.latest(beat.id, now, real_only=True)
            newest = await s.digests.latest(beat.id, now)
        if slot is None and (recent is None or now - recent["at"] > BOOTSTRAP_AFTER):
            # Bootstrap: without any recent digest the first brief would have no shared news at all.
            slot = latest_slot(now, beat.refresh_live_utc, grace=None)
        # A stub run already used this slot's dedupe key: queue the real run under a second key.
        suffix = ":after-stub" if newest is not None and newest["version"] == "stub" else ""
        if slot is not None:
            ids.append(
                await queue.enqueue(
                    "newsroom_run",
                    {"beat": beat.id, "at": slot.isoformat()},
                    dedupe_key=f"newsroom:{beat.id}:{slot:%Y%m%d%H}{suffix}",
                )
            )

    ids.append(await queue.enqueue("spend_guard", {}, dedupe_key=f"spend:{now:%Y%m%d%H}"))
    ids.append(await queue.enqueue("cleanup", {}, dedupe_key=f"cleanup:{now:%Y%m%d}"))
    return [i for i in ids if i is not None]
