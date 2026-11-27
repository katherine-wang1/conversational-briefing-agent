"""Where shared story notes (app/contracts/notes.py) are read from.

A story note is written once per digest story at newsroom time, into the shared table `story_notes`
(story_id text, digest_id text, beat text, note_json jsonb, created_at timestamptz); the latest row by
`created_at` wins. Readers (the voice agent's `get_story`) only need `get(story_id)`.

- `InMemoryNoteStore`: tests, replay and in-process smoke runs.
- `DbNoteStore`: reads `story_notes`. The table's migration belongs to the notes writer; until it
  exists (or on any read error) `get` returns None, so callers fall back to what they already have.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from app.contracts.notes import StoryNote

log = logging.getLogger(__name__)


@runtime_checkable
class NoteStore(Protocol):
    async def get(self, story_id: str) -> StoryNote | None: ...


class InMemoryNoteStore:
    def __init__(self, notes: list[StoryNote] | None = None):
        self._notes: dict[str, StoryNote] = {}
        for n in notes or []:
            self.put(n)

    def put(self, note: StoryNote) -> None:
        cur = self._notes.get(note.story_id)
        if cur is None or note.created_at >= cur.created_at:
            self._notes[note.story_id] = note

    async def get(self, story_id: str) -> StoryNote | None:
        return self._notes.get(story_id)


LATEST_NOTE_SQL = (
    "select note_json from story_notes where story_id = %s order by created_at desc limit 1"
)


class DbNoteStore:
    """Reads the latest note per story from `story_notes`.

    `user_id` given: reads in that user's scope (shared tables are readable there, like `digests`);
    otherwise in a system scope. Missing table, bad rows and DB errors all read as "no note".
    """

    def __init__(self, db: Any, user_id: Any = None, *, run_id: str = "live"):
        self.db = db
        self.user_id = user_id
        self.run_id = run_id
        self.table_missing = False

    def _scope(self):  # noqa: ANN202 - async context manager
        if self.user_id is not None:
            return self.db.user_scope(self.user_id, run_id=self.run_id)
        return self.db.system_scope(run_id=self.run_id)

    async def get(self, story_id: str) -> StoryNote | None:
        if self.table_missing or not story_id:
            return None
        try:
            async with self._scope() as s:
                row = await s.fetch_one(LATEST_NOTE_SQL, [story_id])
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


def _is_undefined_table(e: BaseException) -> bool:
    try:
        from psycopg import errors

        if isinstance(e, errors.UndefinedTable):
            return True
    except ImportError:  # pragma: no cover - psycopg is a dependency
        pass
    return "story_notes" in str(e) and "does not exist" in str(e)
