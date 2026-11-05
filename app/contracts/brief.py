"""Chief-of-staff outputs (TDD §8): candidates, brief script, source packs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.contracts.common import Strict

Slot = Literal["big_news", "for_you", "outside_usual"]
Adjacency = Literal["core", "adjacent", "unrelated"]
CandidateOrigin = Literal["digest", "personal", "adjacent", "carryover", "update"]


class SearchQuery(Strict):
    query: str
    kind: Literal["personal", "adjacent"]
    rationale: str
    memory_refs: list[str] = []  # interest ids


class SearchPlan(Strict):
    queries: list[SearchQuery] = Field(max_length=10)


class CandidateScores(Strict):
    """C4 structured output per candidate."""

    story_ref: str
    relevance: float = Field(ge=0, le=1)
    because: list[str] = []  # memory item ids
    adjacency: Adjacency
    novelty: float = Field(ge=0, le=1)
    importance: int = Field(ge=0, le=100)
    importance_evidence: list[str] = []
    dive_likelihood: float = Field(ge=0, le=1)
    subtopics: list[str] = []
    entities: list[str] = []


class Candidate(Strict):
    story_ref: str  # digest story_id or per-user candidate id
    origin: CandidateOrigin
    headline: str
    summary: str = ""
    urls: list[str] = []
    scores: CandidateScores | None = None


class SelectedStory(Strict):
    story_ref: str
    slot: Slot
    rank: int
    why_tag: Literal["big news", "for you", "outside your usual"]
    mmr_score: float


class Passage(Strict):
    passage_id: str
    doc_id: str
    url: str
    source: str | None = None
    published_at: datetime | None = None
    text: str  # verbatim
    char_start: int
    char_end: int


class Fact(Strict):
    text: str
    passage_ids: list[str]


class RelatedStory(Strict):
    story_ref: str
    headline: str
    shown_on: date | None = None


class SourcePack(Strict):
    story_ref: str
    headline: str
    facts: list[Fact]
    passages: list[Passage]
    reaction: str | None = None
    reaction_passage_ids: list[str] = []
    related: list[RelatedStory] = []
    depth: Literal["deep", "standard", "light"]


class BriefSentence(Strict):
    sid: str  # segment id; audio is rendered per sentence
    text: str
    story_ref: str | None = None
    cites: list[str] = []  # passage ids
    kind: Literal["intro", "followup_answer", "story", "transition", "outro"]


class BriefStory(Strict):
    story_ref: str
    slot: Slot
    headline: str
    why_tag: str
    aliases: list[str] = []  # keywords/entities for reference resolution


class BriefScript(Strict):
    brief_id: str
    user_id: str
    local_date: date
    arm: str = "full"
    stories: list[BriefStory]
    sentences: list[BriefSentence]
    est_duration_s: int


class ClaimCheck(Strict):
    sid: str
    verdict: Literal["supported", "partial", "unsupported"]
    rewritten: str | None = None
    dropped: bool = False


class AudioSegment(Strict):
    sid: str
    url: str
    duration_ms: int
    story_ref: str | None = None


class AudioManifest(Strict):
    brief_id: str
    segments: list[AudioSegment]
