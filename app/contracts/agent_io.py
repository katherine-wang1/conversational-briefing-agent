"""Inputs and outputs of the per-user agents (chief of staff, memory update, follow-up research).

Added by W-D (additive). These are the shapes the job handlers (W-E) assemble from the DB and pass
to `run_cos_daily`, `update_memory_after_session` / `apply_memory_update`, and `run_followup`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.contracts.brief import Passage, Slot
from app.contracts.common import Mode, Strict
from app.contracts.memory import MemorySignal, SessionContext


class UserContext(Strict):
    """Per-run identity and settings for one user's chief-of-staff run."""

    user_id: str
    local_date: date
    now: datetime  # wall clock (live) or simulated "now" (replay); timezone-aware
    mode: Mode = "live"
    run_id: str = "live"
    job_id: str | None = None
    followed_beats: list[str] = []  # beat ids; empty = treat every beat as followed
    arm: str = "full"  # experiment id for the day (recorded on the BriefScript)


class ShownStory(Strict):
    """A story that was put in front of the user (history), with engagement outcome."""

    story_ref: str
    headline: str
    shown_on: date
    slot: Slot | None = None
    urls: list[str] = []
    subtopics: list[str] = []
    entities: list[str] = []
    interest_ids: list[str] = []  # memory items the story was selected for (CandidateScores.because)
    heard: bool = True  # False when the brief ended before it ("not_reached")
    engaged: bool = False  # dive / followup / save
    saved_for_later: bool = False  # carry-over candidate until served again
    update_of: str | None = None


class TranscriptTurn(Strict):
    turn_id: str
    role: Literal["user", "agent"]
    text: str
    story_ref: str | None = None  # story the turn was about, if resolved


class SessionRecord(Strict):
    """Everything memory.reflect and the rules engine need about one finished session."""

    session_id: str
    started_at: datetime
    ctx: SessionContext = SessionContext()
    transcript: list[TranscriptTurn] = []
    signals: list[MemorySignal] = []
    shown: list[ShownStory] = []  # stories in that session's brief (heard or not)


class SessionOutcome(Strict):
    """Compact per-session record kept for the repeated-skip rule (TDD §9.3).

    `apply_memory_update` returns the outcome for the session it processed; callers persist it and
    pass the most recent ones back as `prior_sessions` next time.
    """

    session_id: str
    at: datetime
    opportunity: float
    skipped_interest_ids: list[str] = []


class AnswerSentence(Strict):
    text: str
    cites: list[str] = []  # passage ids


class FollowupAnswer(Strict):
    """Output of follow-up research (TDD §14)."""

    question_id: str
    status: Literal["answered", "unanswerable"]
    answer_sentences: list[AnswerSentence] = []
    what_was_found: str = ""
    passages: list[Passage] = []  # every passage cited by answer_sentences
    researched_at: datetime | None = None


class OpenQuestion(Strict):
    """A bookmarked follow-up question (PRD FUP-2)."""

    id: str
    question: str
    context: dict = {}  # e.g. {"story_ref": ..., "headline": ..., "turn_id": ...}
    status: Literal["open", "researching", "answered", "unanswerable", "dismissed"] = "open"
    created_at: datetime
    answer: FollowupAnswer | None = None
    attached_to_brief: str | None = None  # brief_id once surfaced


class MemoryChange(Strict):
    """One applied (or skipped) change, for audit and failure analysis."""

    interest_id: str | None = None
    label: str
    action: str  # e.g. "reinforce", "weaken", "add", "skip_rule_not_met", "decay", "cap", "floor_drop"
    delta: float = 0.0
    reason: str = ""


class MemoryUpdateReport(Strict):
    session_id: str | None = None
    mode: Literal["learn", "static", "naive"] = "learn"
    opportunity: float = 1.0
    changes: list[MemoryChange] = Field(default_factory=list)
    outcome: SessionOutcome | None = None
