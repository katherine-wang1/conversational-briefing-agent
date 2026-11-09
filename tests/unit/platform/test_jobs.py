from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.config.settings import Settings
from app.contracts.sources import HeadlineObservation
from app.db.ledger import PostgresLedgerSink
from app.db.pool import Database
from app.jobs import scheduler
from app.jobs.deps import Deps
from app.jobs.ports import StubAgents
from app.jobs.queue import JobQueue, backoff_s
from app.jobs.worker import Worker
from app.speech.fake import FakeTTS
from app.speech.storage import LocalDiskStorage
from app.tools import build_tools
from app.tools.headlines import register_source_adapter
from tests.isolation.fixtures import make_user

NOW = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)


def make_deps(db: Database, tmp_path, *, tts=True, now=NOW) -> Deps:  # noqa: ANN001
    return Deps(
        db=db,
        ports=StubAgents(),
        tools=build_tools(Settings(), search_provider="", search_api_key=""),
        storage=LocalDiskStorage(tmp_path / "audio"),
        settings=Settings(credit_openrouter_usd=10.0),
        tts=FakeTTS() if tts else None,
        clock=lambda: now,
    )


@pytest.fixture(autouse=True)
async def clean_jobs(db: Database):
    async with db.system_scope() as s:
        await s.execute("delete from jobs")
    yield
    async with db.system_scope() as s:  # digests made by the news-wait / stub tests below
        await s.execute("delete from digests where id like 'dg-t-%'")


# ---- queue ----


async def test_enqueue_dedupes_and_promotes_priority(db: Database):
    q = JobQueue(db)
    a = await q.enqueue("cos_daily", {"x": 1}, dedupe_key="cos:k")
    b = await q.enqueue("cos_daily", {"x": 2}, dedupe_key="cos:k", priority=0)
    assert a == b
    job = await q.get(a)
    assert job.priority == 0 and job.payload == {"x": 1}
    async with db.system_scope() as s:
        assert await s.jobs.count(dedupe_key="cos:k") == 1


async def test_claim_order_priority_then_run_at(db: Database):
    q = JobQueue(db)
    await q.enqueue("cleanup", dedupe_key="c")  # priority 9
    await q.enqueue("followup_research", dedupe_key="f")  # 2
    await q.enqueue("cos_daily", dedupe_key="later", run_at=datetime.now(UTC) + timedelta(hours=1))
    await q.enqueue("cos_daily", dedupe_key="cos")  # 4
    order = []
    while (j := await q.claim("w1")) is not None:
        order.append(j.dedupe_key)
    assert order == ["f", "cos", "c"]  # 'later' isn't due


async def test_concurrent_claims_never_share_a_job(db: Database):
    q = JobQueue(db)
    for i in range(20):
        await q.enqueue("cleanup", dedupe_key=f"k{i}")
    claimed = await asyncio.gather(*(q.claim(f"w{i}") for i in range(25)))
    ids = [j.id for j in claimed if j]
    assert len(ids) == 20 and len(set(ids)) == 20


async def test_fail_backs_off_then_fails_permanently(db: Database):
    q = JobQueue(db)
    jid = await q.enqueue("cleanup", dedupe_key="x", max_attempts=2)
    j = await q.claim("w")
    assert await q.fail(j, "boom") == "queued"
    again = await q.get(jid)
    assert again.status == "queued" and again.last_error == "boom"
    assert await q.claim("w") is None  # backoff: not due yet
    async with db.system_scope() as s:
        await s.execute("update jobs set run_at = now() where id = %s", [jid])
    j2 = await q.claim("w")
    assert j2.attempts == 2
    assert await q.fail(j2, "boom2") == "failed"
    assert (await q.get(jid)).status == "failed"
    assert backoff_s(1) == 30 and backoff_s(3) == 120 and backoff_s(30) == 3600


async def test_reap_stale_requeues(db: Database):
    q = JobQueue(db)
    jid = await q.enqueue("cleanup", dedupe_key="stale")
    await q.claim("dead-worker")
    async with db.system_scope() as s:
        await s.execute("update jobs set locked_at = now() - interval '1 hour' where id = %s", [jid])
    assert await q.reap_stale() == 1
    assert (await q.get(jid)).status == "queued"


async def test_worker_unknown_type_and_handler_errors(db: Database, tmp_path):
    deps = make_deps(db, tmp_path)
    calls = []

    async def flaky(deps, job):  # noqa: ANN001
        calls.append(job.attempts)
        raise RuntimeError("transient")

    w = Worker(deps, handlers={"flaky": flaky}, run_scheduler=False)
    a = await deps.queue.enqueue("nope", dedupe_key="nope")
    b = await deps.queue.enqueue("flaky", dedupe_key="flaky", max_attempts=3)
    await w.drain()
    assert (await deps.queue.get(a)).status == "failed"
    jb = await deps.queue.get(b)
    assert jb.status == "queued" and "transient" in jb.last_error and calls == [1]


# ---- scheduler ----


def test_cos_due_respects_timezone_and_lead():
    la = "America/Los_Angeles"
    # 07:00 PT brief → due from 06:00 PT (14:00 UTC in November, PST = UTC-8)
    assert scheduler.cos_due(datetime(2026, 11, 5, 13, 59, tzinfo=UTC), la, time(7), 60) is None
    assert scheduler.cos_due(datetime(2026, 11, 5, 14, 0, tzinfo=UTC), la, time(7), 60) == date(2026, 11, 5)
    # brief at 00:30 → the next day's job is due at 23:30 the evening before
    assert scheduler.cos_due(datetime(2026, 11, 6, 7, 31, tzinfo=UTC), la, time(0, 30), 60) == date(
        2026, 11, 6
    )
    assert scheduler.cos_due(datetime(2026, 11, 5, 14, 0, tzinfo=UTC), "Not/AZone", time(14), 0) == date(
        2026, 11, 5
    )


def test_latest_slot():
    slots = ["06:00", "12:00", "18:00"]
    assert scheduler.latest_slot(datetime(2026, 11, 5, 13, 0, tzinfo=UTC), slots) == datetime(
        2026, 11, 5, 12, tzinfo=UTC
    )
    assert scheduler.latest_slot(datetime(2026, 11, 5, 16, 0, tzinfo=UTC), slots) is None  # > 3 h late
    assert scheduler.latest_slot(datetime(2026, 11, 5, 1, 0, tzinfo=UTC), slots) is None
    assert scheduler.latest_slot(datetime(2026, 11, 5, 16, 0, tzinfo=UTC), slots, grace=None) == datetime(
        2026, 11, 5, 12, tzinfo=UTC
    )


async def test_tick_is_idempotent(db: Database):
    q = JobQueue(db)
    uid = await make_user(db, timezone="America/Los_Angeles", brief_time=time(7))
    now = datetime(2026, 11, 5, 14, 5, tzinfo=UTC)
    first = await scheduler.tick(db, q, now)
    await scheduler.tick(db, q, now + timedelta(seconds=60))
    async with db.system_scope() as s:
        cos = await s.jobs.list(type="cos_daily")
        assert [j["dedupe_key"] for j in cos if j["user_id"] == uid] == [f"cos:{uid}:2026-11-05"]
        # ai: its 12:00 UTC slot; energy / physical_ai: no digest yet, so bootstrapped from 06:00
        runs = await s.jobs.list(type="newsroom_run")
        assert sorted(j["dedupe_key"] for j in runs) == [
            "newsroom:ai:2026110512",
            "newsroom:energy:2026110506",
            "newsroom:physical_ai:2026110506",
        ]
        assert await s.jobs.count(type="spend_guard") == 1
    assert first


async def test_tick_skips_bootstrap_when_digests_are_recent(db: Database):
    q = JobQueue(db)
    now = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)  # > 3 h after every beat's latest slot
    async with db.system_scope() as s:
        for id_, beat, age_h in (("dg_e", "energy", 10), ("dg_a", "ai", 30)):
            at = now - timedelta(hours=age_h)
            await s.digests.insert(id=id_, run_id="live", beat=beat, at=at, version=1)
    await scheduler.tick(db, q, now)
    async with db.system_scope() as s:
        keys = sorted(j["dedupe_key"] for j in await s.jobs.list(type="newsroom_run"))
    # energy has a 10 h old digest: wait for its next slot; ai (30 h old) and physical_ai (none) bootstrap
    assert keys == ["newsroom:ai:2026110512", "newsroom:physical_ai:2026110506"]


async def test_tick_reruns_a_beat_whose_only_digest_is_a_stub_placeholder(db: Database):
    q = JobQueue(db)
    now = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)  # > 3 h after every beat's latest slot
    async with db.system_scope() as s:
        await s.execute("delete from digests where run_id = 'live'")
        for beat in ("ai", "energy", "physical_ai"):
            # energy: a real digest; ai: only a stub placeholder (a worker started without a key)
            version = "stub" if beat == "ai" else "v"
            await s.digests.insert(
                id=f"dg-t-{beat}", run_id="live", beat=beat, at=now - timedelta(hours=2), version=version
            )
        assert (await s.digests.latest("ai", now, real_only=True)) is None
        assert (await s.digests.latest("ai", now))["version"] == "stub"
    await q.enqueue("newsroom_run", {"beat": "ai"}, dedupe_key="newsroom:ai:2026110512")  # the stub's run
    await scheduler.tick(db, q, now)
    async with db.system_scope() as s:
        keys = sorted(j["dedupe_key"] for j in await s.jobs.list(type="newsroom_run"))
    assert keys == ["newsroom:ai:2026110512", "newsroom:ai:2026110512:after-stub"]


# ---- handlers end to end (stub agents, fake TTS) ----


async def test_onboarding_to_ready_brief(db: Database, tmp_path):
    deps = make_deps(db, tmp_path)
    uid = await make_user(db, timezone="UTC")
    async with db.system_scope() as s:
        await s.digests.insert(id="dg1", beat="ai", at=NOW - timedelta(hours=2), version="v")
        await s.digest_entries.insert(
            digest_id="dg1",
            story_id="st1",
            headline="Acme ships Voltaic",
            summary="Acme shipped it.",
            importance=80,
            urls=["https://x"],
        )
    await deps.queue.enqueue(
        "onboarding_seed", {"text": "agent memory, grid batteries"}, user_id=uid, dedupe_key=f"onb:{uid}"
    )
    ran = await Worker(deps, run_scheduler=False).drain()
    assert [j.type for j in ran] == ["onboarding_seed", "cos_daily", "tts_render"]
    async with db.user_scope(uid) as s:
        assert {i["label"] for i in await s.interests.list()} == {"agent memory", "grid batteries"}
        b = await s.briefs.find_one(local_date=date(2026, 11, 5))
        assert b["status"] == "ready" and b["arm"] == "full"
        assert b["script"]["arm"] == "stub"
        segs = b["audio_manifest"]["segments"]
        assert len(segs) == len(b["script"]["sentences"])
        assert (tmp_path / "audio" / segs[0]["url"]).read_bytes().startswith(b"FAKE-MP3:")
        assert await s.source_packs.count(brief_id=b["id"]) == 1


async def test_cos_daily_waits_for_a_pending_onboarding_seed(db: Database, tmp_path, monkeypatch):
    from app.jobs import handlers

    monkeypatch.setattr(handlers, "SEED_POLL_S", 0.02)
    deps = make_deps(db, tmp_path)
    uid = await make_user(db, timezone="UTC")
    # Onboarding queued the seed; GET /api/briefs/today queued cos_daily right after it.
    seed_id = await deps.queue.enqueue("onboarding_seed", {"text": "x"}, user_id=uid, dedupe_key="onb")
    await deps.queue.enqueue("cos_daily", {"local_date": "2026-11-05"}, user_id=uid, dedupe_key="cos")
    seed = await deps.queue.claim("w1", ["onboarding_seed"])  # another worker slot is running it
    assert seed is not None and seed.id == seed_id
    cos = await deps.queue.claim("w2", ["cos_daily"])
    task = asyncio.create_task(Worker(deps, run_scheduler=False).execute(cos))
    await asyncio.sleep(0.2)
    assert not task.done()  # still waiting for the interests
    await deps.queue.complete(seed_id)
    assert await asyncio.wait_for(task, 5) == "done"
    async with db.user_scope(uid) as s:
        assert (await s.briefs.find_one(local_date=date(2026, 11, 5)))["status"] == "rendering"


async def test_cos_daily_gives_up_waiting_for_a_stuck_seed(db: Database, tmp_path, monkeypatch):
    from app.jobs import handlers

    monkeypatch.setattr(handlers, "SEED_POLL_S", 0.02)
    monkeypatch.setattr(handlers, "SEED_WAIT_S", 0.1)
    deps = make_deps(db, tmp_path)
    uid = await make_user(db, timezone="UTC")
    await deps.queue.enqueue("onboarding_seed", {"text": "x"}, user_id=uid, dedupe_key="onb")
    await deps.queue.claim("w1", ["onboarding_seed"])
    await deps.queue.enqueue("cos_daily", {"local_date": "2026-11-05"}, user_id=uid, dedupe_key="cos")
    cos = await deps.queue.claim("w2", ["cos_daily"])
    assert await asyncio.wait_for(Worker(deps, run_scheduler=False).execute(cos), 5) == "done"


async def _fresh_digests(db: Database, beats, age=timedelta(hours=1)) -> None:  # noqa: ANN001
    async with db.system_scope() as s:
        await s.execute("delete from digests where run_id = 'live'")
        for b in beats:
            await s.digests.insert(id=f"dg-t-{b}", run_id="live", beat=b, at=NOW - age, version="v")


async def test_news_pending_only_while_first_digests_are_missing(db: Database):
    from app.config import loader
    from app.jobs import handlers

    beats = list(loader.beats())
    q = JobQueue(db)
    await _fresh_digests(db, beats[:-1])  # the last beat has no digest yet
    async with db.system_scope() as s:
        assert not await handlers.news_pending(s, NOW)  # nothing running: nothing to wait for
    await q.enqueue("newsroom_run", {"beat": beats[-1]}, dedupe_key="nr-first")
    async with db.system_scope() as s:
        assert await handlers.news_pending(s, NOW)
    await _fresh_digests(db, beats)  # every beat has recent news: a scheduled refresh doesn't block
    async with db.system_scope() as s:
        assert not await handlers.news_pending(s, NOW)
    await _fresh_digests(db, beats, age=timedelta(hours=30))  # stale everywhere: wait for the refresh
    async with db.system_scope() as s:
        assert await handlers.news_pending(s, NOW)


async def test_cos_daily_waits_for_the_first_news_digests(db: Database, tmp_path, monkeypatch):
    from app.config import loader
    from app.jobs import handlers

    monkeypatch.setattr(handlers, "NEWS_POLL_S", 0.02)
    beats = list(loader.beats())
    await _fresh_digests(db, [])
    deps = make_deps(db, tmp_path)
    uid = await make_user(db, timezone="UTC")
    nr_id = await deps.queue.enqueue("newsroom_run", {"beat": beats[0]}, dedupe_key="nr")
    assert (await deps.queue.claim("w1", ["newsroom_run"])).id == nr_id  # running in another slot
    await deps.queue.enqueue("cos_daily", {"local_date": "2026-11-05"}, user_id=uid, dedupe_key="cos")
    cos = await deps.queue.claim("w2", ["cos_daily"])
    task = asyncio.create_task(Worker(deps, run_scheduler=False).execute(cos))
    await asyncio.sleep(0.2)
    assert not task.done()  # still gathering news
    await _fresh_digests(db, beats)
    await deps.queue.complete(nr_id)
    assert await asyncio.wait_for(task, 5) == "done"


async def test_cos_daily_gives_up_waiting_for_stuck_news(db: Database, tmp_path, monkeypatch):
    from app.jobs import handlers

    monkeypatch.setattr(handlers, "NEWS_POLL_S", 0.02)
    monkeypatch.setattr(handlers, "NEWS_WAIT_S", 0.1)
    await _fresh_digests(db, [])
    deps = make_deps(db, tmp_path)
    uid = await make_user(db, timezone="UTC")
    await deps.queue.enqueue("newsroom_run", {"beat": "ai"}, dedupe_key="nr")
    await deps.queue.claim("w1", ["newsroom_run"])
    await deps.queue.enqueue("cos_daily", {"local_date": "2026-11-05"}, user_id=uid, dedupe_key="cos")
    cos = await deps.queue.claim("w2", ["cos_daily"])
    assert await asyncio.wait_for(Worker(deps, run_scheduler=False).execute(cos), 5) == "done"


async def test_brief_without_tts_is_ready_without_audio(db: Database, tmp_path):
    deps = make_deps(db, tmp_path, tts=False)
    uid = await make_user(db, timezone="UTC")
    await deps.queue.enqueue("cos_daily", {"local_date": "2026-11-05"}, user_id=uid, dedupe_key="c")
    await Worker(deps, run_scheduler=False).drain()
    async with db.user_scope(uid) as s:
        b = await s.briefs.find_one(local_date=date(2026, 11, 5))
    assert b["status"] == "ready" and b["audio_manifest"] is None


async def test_newsroom_run_writes_shared_digest(db: Database, tmp_path):
    async def fake_adapter(cfg, beat, at):  # noqa: ANN001
        return [
            HeadlineObservation(
                source_id=cfg.id,
                beat=beat,
                observed_at=at,
                url=f"https://x/{cfg.id}",
                title=f"Story from {cfg.id}",
                rank=1,
                extra={"feed_position": 1},  # real adapters set it (jsonb column)
            )
        ]

    for t in ("techmeme", "hn_front", "hf_papers", "reddit_top", "arxiv", "rss", "frontpage"):
        register_source_adapter(t, fake_adapter)
    deps = make_deps(db, tmp_path)
    at = datetime(2026, 11, 5, 12, tzinfo=UTC)
    await deps.queue.enqueue("newsroom_run", {"beat": "ai", "at": at.isoformat()}, dedupe_key="nr")
    await Worker(deps, run_scheduler=False).drain()
    async with db.system_scope() as s:
        d = await s.digests.get("dg-stub-ai-2026110512")
        assert d is not None and d["version"] == "stub"
        assert await s.digest_entries.count(digest_id=d["id"]) > 5
        assert await s.headline_observations.count(beat="ai") > 5
        assert (await s.headline_observations.find_one(beat="ai"))["extra"] == {"feed_position": 1}
        assert (await s.newsroom_runs.find_one(beat="ai"))["status"] == "done"


async def test_memory_update_followup_snapshot(db: Database, tmp_path):
    deps = make_deps(db, tmp_path)
    uid = await make_user(db, timezone="UTC")
    async with db.user_scope(uid) as s:
        await s.interests.insert(
            id="i1", label="batteries", subtopic="other", weight=0.4, confidence=0.3, source="onboarding"
        )
        await s.sessions.insert(id="sess1", end_reason="natural")
        await s.turns.insert(id="t1", session_id="sess1", role="user", text="who funded it?")
        await s.memory_signals.insert(session_id="sess1", type="dive", story_ref="st1")
        await s.open_questions.insert(id="q1", question="who funded it?")
    await deps.queue.enqueue("memory_update", {"session_id": "sess1"}, user_id=uid, dedupe_key="m")
    await deps.queue.enqueue("followup_research", {"question_id": "q1"}, user_id=uid, dedupe_key="f")
    await deps.queue.enqueue("daily_snapshot", {"local_date": "2026-11-05"}, user_id=uid, dedupe_key="s")
    await Worker(deps, run_scheduler=False).drain()
    async with db.user_scope(uid) as s:
        assert (await s.interests.find_one(id="i1"))["label"] == "batteries"  # stub: unchanged
        q = await s.open_questions.get("q1")
        assert q["status"] == "unanswerable" and "placeholder" in q["answer"]["what_was_found"]
        snap = await s.memory_snapshots.find_one(as_of_date=date(2026, 11, 5))
        assert snap["payload"]["interests"][0]["id"] == "i1"
    async with db.system_scope() as s:
        assert {j["status"] for j in await s.jobs.list()} == {"done"}


async def test_spend_guard_alerts_and_pauses_eval_jobs(db: Database, tmp_path):
    deps = make_deps(db, tmp_path)  # openrouter credit $10
    sink = PostgresLedgerSink(db)
    await sink.write(
        {
            "role": "cos.score",
            "provider": "openrouter",
            "model": "m",
            "cost_usd": 6.5,
            "tokens_in": 10,
            "trace": {"run_id": "live"},
        }
    )
    async with db.system_scope() as s:
        await s.execute("delete from admin_alerts")
    eval_job = await deps.queue.enqueue("eval_run", dedupe_key="ev")
    await deps.queue.enqueue("spend_guard", dedupe_key="sg")
    await Worker(deps, run_scheduler=False).drain(types=["spend_guard"])
    async with db.system_scope() as s:
        keys = {a["key"] for a in await s.admin_alerts.list()}
    assert keys == {"spend:openrouter:40", "spend:openrouter:60"}
    assert (await deps.queue.get(eval_job)).status == "paused"


async def test_delete_user_purges_storage_and_ledger_link(db: Database, tmp_path):
    deps = make_deps(db, tmp_path)
    uid = await make_user(db)
    await deps.storage.put(f"{uid}/b1/s0.mp3", b"x")
    await PostgresLedgerSink(db).write(
        {"role": "voice.turn", "provider": "fake", "model": "m", "user_id": uid}
    )
    async with db.user_scope(uid) as s:
        await s.profiles.delete(uid)
    await deps.queue.enqueue("delete_user", user_id=uid, dedupe_key=f"del:{uid}")
    await Worker(deps, run_scheduler=False).drain()
    assert not (tmp_path / "audio" / str(uid)).exists() or not any(
        (tmp_path / "audio" / str(uid)).rglob("*.mp3")
    )
    async with db.system_scope() as s:
        assert await s.llm_calls.count(user_id=uid) == 0
