"""Where shared story notes are read from: `NoteStore.get(story_id)` returns the latest note for a digest
story, or None. The per-user stored set and the voice agent's `get_story` read notes through this
protocol; the newsroom writes them (app/agents/newsroom/notes.py, app/jobs/handlers.py).

- `InMemoryNoteStore`: tests, smoke scripts and in-process replay.
- `DbNoteStore`: the shared `story_notes` table (latest row by created_at per story_id and run_id).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from app.contracts.notes import StoryNote


@runtime_checkable
class NoteStore(Protocol):
    async def get(self, story_id: str) -> StoryNote | None: ...


class InMemoryNoteStore:
    """Latest note per story_id (by created_at; a later put with an equal time wins)."""

    def __init__(self, notes: Iterable[StoryNote] = ()) -> None:
        self.notes: dict[str, StoryNote] = {}
        for n in notes:
            self._put(n)

    def _put(self, note: StoryNote) -> None:
        cur = self.notes.get(note.story_id)
        if cur is None or note.created_at >= cur.created_at:
            self.notes[note.story_id] = note

    async def get(self, story_id: str) -> StoryNote | None:
        return self.notes.get(story_id)

    async def put(self, note: StoryNote) -> None:
        self._put(note)

    async def put_many(self, notes: Iterable[StoryNote]) -> int:
        n = 0
        for note in notes:
            self._put(note)
            n += 1
        return n


class DbNoteStore:
    """The shared `story_notes` table (no user data; read and written from a system scope)."""

    def __init__(self, db: Any, run_id: str = "live") -> None:  # db: app.db.pool.Database
        self.db = db
        self.run_id = run_id

    async def get(self, story_id: str) -> StoryNote | None:
        async with self.db.system_scope(run_id=self.run_id) as s:
            return await s.story_notes.get_note(story_id, run_id=self.run_id)

    async def put(self, note: StoryNote) -> None:
        async with self.db.system_scope(run_id=self.run_id) as s:
            await s.story_notes.put(note, run_id=self.run_id)

    async def put_many(self, notes: Iterable[StoryNote]) -> int:
        notes = list(notes)
        async with self.db.system_scope(run_id=self.run_id) as s:
            for note in notes:
                await s.story_notes.put(note, run_id=self.run_id)
        return len(notes)


__all__ = ["DbNoteStore", "InMemoryNoteStore", "NoteStore"]
