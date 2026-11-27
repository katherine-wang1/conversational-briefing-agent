"""Where shared story notes (app/contracts/notes.py) are read from and written to.

A story note is written once per digest story at newsroom time (app/agents/newsroom/notes.py,
app/jobs/handlers.py) into the shared table `story_notes`; the latest row by `created_at` for a story
and run wins. Readers (the voice agent's `get_story`) only need `get(story_id)`.

- `InMemoryNoteStore`: tests, smoke scripts and in-process replay.
- `DbNoteStore`: the `story_notes` table. Reads never raise: a missing table, a bad row or a DB error
  reads as "no note", so a voice turn falls back to what it already has.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from app.contracts.notes import StoryNote

log = logging.getLogger(__name__)


@runtime_checkable
class NoteStore(Protocol):
    async def get(self, story_id: str) -> StoryNote | None: ...


class InMemoryNoteStore:
    """Latest note per story_id (by created_at; a later put with an equal time wins)."""

    def __init__(self, notes: Iterable[StoryNote] | None = None) -> None:
        self.notes: dict[str, StoryNote] = {}
        for n in notes or ():
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


LATEST_NOTE_SQL = (
    "select note_json from story_notes where story_id = %s and run_id = %s order by created_at desc limit 1"
)


class DbNoteStore:
    """The shared `story_notes` table (no user data).

    Reads run in `user_id`'s scope when given (shared tables are readable there, like `digests`), else
    in a system scope; writes always use a system scope. `run_id` keeps replay notes apart from live ones.
    """

    def __init__(self, db: Any, user_id: Any = None, *, run_id: str = "live") -> None:
        self.db = db  # app.db.pool.Database
        self.user_id = user_id
        self.run_id = run_id
        self.table_missing = False

    def _read_scope(self):  # noqa: ANN202 - async context manager
        if self.user_id is not None:
            return self.db.user_scope(self.user_id, run_id=self.run_id)
        return self.db.system_scope(run_id=self.run_id)

    async def get(self, story_id: str) -> StoryNote | None:
        if self.table_missing or not story_id:
            return None
        try:
            async with self._read_scope() as s:
                row = await s.fetch_one(LATEST_NOTE_SQL, [story_id, self.run_id])
        except Exception as e:  # noqa: BLE001 - a missing note must never break a voice turn
            if _is_undefined_table(e):
                self.table_missing = True
                log.info("story_notes table not found; get_story falls back to the stored story")
            else:
                log.warning("story note read failed for %s: %s", story_id, type(e).__name__)
            return None
        if row is None:
            return None
        raw = row.get("note_json") if isinstance(row, dict) else row[0]
        try:
            return StoryNote.model_validate(raw)
        except ValidationError:
            log.warning("story note for %s does not match StoryNote; ignored", story_id)
            return None

    async def put(self, note: StoryNote) -> None:
        async with self.db.system_scope(run_id=self.run_id) as s:
            await s.story_notes.put(note, run_id=self.run_id)

    async def put_many(self, notes: Iterable[StoryNote]) -> int:
        notes = list(notes)
        async with self.db.system_scope(run_id=self.run_id) as s:
            for note in notes:
                await s.story_notes.put(note, run_id=self.run_id)
        return len(notes)


def _is_undefined_table(e: BaseException) -> bool:
    try:
        from psycopg import errors

        if isinstance(e, errors.UndefinedTable):
            return True
    except ImportError:  # pragma: no cover - psycopg is a dependency
        pass
    return "story_notes" in str(e) and "does not exist" in str(e)


__all__ = ["DbNoteStore", "InMemoryNoteStore", "NoteStore"]
