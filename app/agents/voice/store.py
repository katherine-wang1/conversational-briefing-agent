"""Persistence for a live session: turns, signals, open questions, saved items, session row, and
the jobs a session triggers. `DbSessionStore` writes in the session owner's scope; `MemorySessionStore`
is for tests and the simulator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.db.ids import new_id


class SessionStore(Protocol):
    async def add_turn(self, turn: dict[str, Any]) -> None: ...

    async def add_signal(self, signal: dict[str, Any]) -> None: ...

    async def create_question(self, question: str, story_ref: str | None, context: dict[str, Any]) -> str: ...

    async def save_item(self, story_id: str) -> None: ...

    async def update_session(self, **fields: Any) -> None: ...

    async def on_end(self) -> None: ...


class MemorySessionStore:
    def __init__(self, session_id: str = "sess"):
        self.session_id = session_id
        self.turns: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self.saved: list[str] = []
        self.session: dict[str, Any] = {}
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def add_turn(self, turn: dict[str, Any]) -> None:
        self.turns.append(turn)

    async def add_signal(self, signal: dict[str, Any]) -> None:
        self.signals.append(signal)

    async def create_question(self, question: str, story_ref: str | None, context: dict[str, Any]) -> str:
        qid = new_id("q")
        self.questions.append({"id": qid, "question": question, "story_ref": story_ref, "context": context})
        self.jobs.append(("followup_research", {"question_id": qid}))
        return qid

    async def save_item(self, story_id: str) -> None:
        self.saved.append(story_id)

    async def update_session(self, **fields: Any) -> None:
        self.session.update(fields)

    async def on_end(self) -> None:
        self.jobs.append(("memory_update", {"session_id": self.session_id}))


class DbSessionStore:
    def __init__(self, db: Any, user_id: UUID, session_id: str, run_id: str = "live"):
        from app.jobs.queue import JobQueue

        self.db = db
        self.queue = JobQueue(db)
        self.user_id = user_id
        self.session_id = session_id
        self.run_id = run_id

    def _scope(self):  # noqa: ANN202
        return self.db.user_scope(self.user_id, run_id=self.run_id)

    async def add_turn(self, turn: dict[str, Any]) -> None:
        async with self._scope() as s:
            await s.turns.insert(session_id=self.session_id, **turn)

    async def add_signal(self, signal: dict[str, Any]) -> None:
        async with self._scope() as s:
            await s.memory_signals.insert(session_id=self.session_id, **signal)

    async def create_question(self, question: str, story_ref: str | None, context: dict[str, Any]) -> str:
        qid = new_id("q")
        async with self._scope() as s:
            await s.open_questions.insert(
                id=qid, session_id=self.session_id, story_ref=story_ref, question=question, context=context
            )
        await self.queue.enqueue(
            "followup_research",
            {"question_id": qid},
            user_id=self.user_id,
            run_id=self.run_id,
            dedupe_key=f"fup:{qid}",
        )
        return qid

    async def save_item(self, story_id: str) -> None:
        async with self._scope() as s:
            await s.saved_items.upsert(
                conflict=("user_id", "run_id", "story_id"), story_id=story_id, session_id=self.session_id
            )

    async def update_session(self, **fields: Any) -> None:
        async with self._scope() as s:
            await s.sessions.update(self.session_id, **fields)

    async def on_end(self) -> None:
        await self.queue.enqueue(
            "memory_update",
            {"session_id": self.session_id},
            user_id=self.user_id,
            run_id=self.run_id,
            dedupe_key=f"mem:{self.session_id}",
        )


def utcnow() -> datetime:
    return datetime.now(UTC)
