"""Memory contracts (TDD §9)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.contracts.common import Strict

InterestKind = Literal["topic", "entity", "keep_posted"]
InterestSource = Literal["onboarding", "explicit", "implicit", "exploration"]

SignalType = Literal[
    "dive",
    "followup",
    "save_for_later",
    "bookmark",
    "more_like",
    "less_like",
    "stop_covering",
    "keep_posted",
    "skip",
    "not_reached",
    "delivery_feedback",
    "time_available",
]


class Interest(Strict):
    id: str
    label: str  # open text, user's phrasing
    subtopic: str  # taxonomy id ("other" if none)
    entity: str | None = None
    kind: InterestKind = "topic"
    weight: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    explicit_pin: bool = False
    shown_n: int = 0
    engaged_n: int = 0
    last_reinforced_at: datetime | None = None
    source: InterestSource
    evidence: list[str] = []  # turn / session ids


class DeliveryPref(Strict):
    key: Literal["length_s", "depth", "technicality", "style", "preferred_sources"]
    value: str | int | float | list[str]
    confidence: float = Field(ge=0, le=1)


class SessionContext(Strict):
    time_available_s: int | None = None
    brief_length_s: int | None = None
    end_reason: Literal["natural", "cut_short", "idle", "error"] | None = None


class MemorySignal(Strict):
    session_id: str
    type: SignalType
    story_ref: str | None = None
    interest_hint: str | None = None
    strength: float = 1.0
    explicit: bool = False
    turn_id: str | None = None
    payload: dict = {}
    created_at: datetime


class ProposalOp(Strict):
    op: Literal["reinforce", "weaken", "add", "pin", "unpin", "remove"]
    label: str
    subtopic: str
    entity: str | None = None
    kind: InterestKind = "topic"
    strength: Literal["explicit", "implicit"]
    signal: SignalType
    evidence: list[str] = []
    rationale: str = ""


class DeliveryChange(Strict):
    key: Literal["length_s", "depth", "technicality", "style", "preferred_sources"]
    value: str | int | float | list[str]
    explicit: bool
    evidence: list[str] = []


class MemoryProposal(Strict):
    """memory.reflect structured output. Code applies it via the rules engine (TDD §9.3)."""

    ops: list[ProposalOp]
    delivery: list[DeliveryChange] = []


class MemorySnapshot(Strict):
    user_id: str
    run_id: str = "live"
    as_of: date
    interests: list[Interest]
    delivery: list[DeliveryPref]
    profile_text: str | None = None  # B5 naive arm only: free-text profile (explicit statements appended)
