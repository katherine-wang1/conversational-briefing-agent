"""Grader input records. Graders are pure functions over these; `eval/graders/from_run.py` builds
them from runner traces. Nothing here is an agent contract: it is the evaluation side's view."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class ShownStory(BaseModel):
    """One story in one user's brief on one day, plus what the simulated user did with it."""

    user_id: str  # profile id
    day: date
    day_index: int  # 0-based offset in the window (drift schedule uses this)
    story_ref: str
    label_story_id: str | None = None  # hindsight-label story id this maps to (digest stories: same id)
    rank: int = 0  # position in the brief (0 = first)
    slot: str | None = None
    headline: str = ""
    text: str = ""  # headline + summary/brief sentences, for keyword matching
    subtopics: list[str] = []
    entities: list[str] = []
    heard: Literal["full", "partial", "none"] = "full"
    engaged: bool = False


class QuestionTurn(BaseModel):
    """One user question in a session, with what the agent did."""

    qid: str
    user_id: str
    day: date
    text: str
    answerability: Literal["answerable", "needs_search", "unanswerable", "unknown"] = "unknown"
    tools: list[str] = []  # tool names the voice agent used on this turn
    outcome: Literal["answered", "abstained", "bookmarked"] = "answered"
    judge_label: str | None = None  # QA judge: correct_supported / incorrect / unsupported / abstained
    ttft_ms: float | None = None
    end_of_speech_ts: float | None = None
    first_tts_chunk_ts: float | None = None


class Bookmark(BaseModel):
    qid: str
    user_id: str
    answerable: bool | None  # None = unknown
    created_at: datetime
    answered_at: datetime | None = None  # when the answer was ready (None = unresolved)
    next_brief_at: datetime  # when the user's next brief was delivered
    claims_total: int = 0
    claims_supported: int = 0
