"""`story_notes` table, repo, DbNoteStore and the newsroom job's note step (Postgres; skipped without it)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.agents.newsroom.note_store import DbNoteStore
from app.config.settings import Settings
from app.contracts.notes import NoteFact, NoteSource, StoryNote
from app.db.pool import Database
from app.jobs import handlers
from app.jobs.deps import Deps
from app.jobs.ports import StubAgents
from app.speech.storage import LocalDiskStorage
from tests.unit.cos.fakes import FakeModelClient
from tests.unit.newsroom.test_story_notes import TEXT_A, Fetch, digest, doc, entry, note_handler

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def note(story_id: str, digest_id: str, at: datetime, *, facts: int = 1) -> StoryNote:
    return StoryNote(
        story_id=story_id,
        digest_id=digest_id,
        beat="ai",
        headline=f"{story_id} headline",
        facts=[NoteFact(text=f"fact {i}", source_urls=["https://a.com/x"]) for i in range(facts)],
        sources=[NoteSource(url="https://a.com/x")],
        created_at=at,
    )


async def test_repo_latest_by_created_at_and_run_id(db: Database):
    async with db.system_scope() as s:
        await s.execute("delete from story_notes")
        await s.story_notes.put(note("st-1", "dg-a", NOW, facts=1))
        await s.story_notes.put(note("st-1", "dg-b", NOW + timedelta(hours=2), facts=3))
        await s.story_notes.put(note("st-1", "dg-c", NOW + timedelta(hours=1)))  # older, written later
        await s.story_notes.put(note("st-2", "dg-b", NOW + timedelta(hours=2)))
        await s.story_notes.put(note("st-1", "dg-x", NOW + timedelta(hours=9)), run_id="eval-1")
        got = await s.story_notes.get_note("st-1")
        assert got.digest_id == "dg-b" and len(got.facts) == 3
        assert got.created_at == NOW + timedelta(hours=2)
        assert await s.story_notes.get_note("missing") is None
        assert {n.story_id for n in await s.story_notes.for_digest("dg-b")} == {"st-1", "st-2"}
        assert (await s.story_notes.get_note("st-1", run_id="eval-1")).digest_id == "dg-x"
        row = await s.story_notes.latest("st-2")
        assert row["beat"] == "ai" and row["digest_id"] == "dg-b" and row["run_id"] == "live"


async def test_db_note_store_round_trip(db: Database):
    store = DbNoteStore(db)
    assert (
        await store.put_many([note("st-9", "dg-1", NOW), note("st-9", "dg-2", NOW + timedelta(minutes=5))])
        == 2
    )
    assert (await store.get("st-9")).digest_id == "dg-2"
    assert await DbNoteStore(db, run_id="eval-2").get("st-9") is None


def deps_for(db: Database, tmp_path, client, tools, *, real: bool = True) -> Deps:  # noqa: ANN001
    ports = SimpleNamespace(name="real") if real else StubAgents()
    return Deps(
        db=db,
        ports=ports,
        tools=tools,
        storage=LocalDiskStorage(tmp_path / "audio"),
        settings=Settings(),
        model_client=client,
        clock=lambda: NOW,
    )


async def test_newsroom_job_note_step_stores_notes_and_skips_without_a_model(db: Database, tmp_path):
    a = "https://robotreport.com/acme"
    tools = {"fetch": Fetch({a: doc(a, TEXT_A)})}
    d = digest(entry("st-job-1", [a]), entry("st-job-2", ["https://dead.example/x"]))

    assert await handlers.write_digest_notes(deps_for(db, tmp_path, None, tools), d) == 0  # no key
    client = FakeModelClient({"newsroom.story_note": note_handler})
    assert await handlers.write_digest_notes(deps_for(db, tmp_path, client, tools, real=False), d) == 0
    async with db.system_scope() as s:
        assert await s.story_notes.get_note("st-job-1") is None

    n = await handlers.write_digest_notes(deps_for(db, tmp_path, client, tools), d, job_id="j1")
    assert n == 2
    store = DbNoteStore(db)
    ok, empty = await store.get("st-job-1"), await store.get("st-job-2")
    assert ok.status == "ok" and ok.facts and ok.digest_id == d.digest_id
    assert empty.status == "no_text" and empty.facts == []
