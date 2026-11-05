"""Newsroom outputs (TDD §7)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.contracts.common import Strict


class StoryFeatures(Strict):
    """Code-computed signal features (N4). Engagement values are as observed, never later."""

    n_items: int = 0
    n_independent_domains: int = 0
    best_tier: int | None = None  # 1 = highest
    frontpage_hits: int = 0  # (source, run) pairs where it was on a front page
    best_rank: int | None = None
    hn_points_at_observation: int | None = None
    comments_at_observation: int | None = None
    first_seen: datetime | None = None
    velocity: int = 0  # new items since the last run
    has_primary_source: bool = False


class Story(Strict):
    story_id: str
    beat: str
    title: str
    first_seen: datetime
    item_urls: list[str]
    features: StoryFeatures
    update_of: str | None = None


class DigestEntryLLM(Strict):
    """N5 structured output per story."""

    story_id: str
    headline: str  # specific: names actors and objects (UX-2)
    summary: str  # 1–2 sentences, from snippets only
    subtopics: list[str]  # taxonomy ids
    entities: list[str]
    importance: int = Field(ge=0, le=100)
    evidence: list[str]  # references to features / sources
    is_update_of: str | None = None
    notes_for_editor: str | None = None


class DigestEntry(DigestEntryLLM):
    importance_rules: float | None = None  # rules-only score from features
    features: StoryFeatures
    urls: list[str]


class Digest(Strict):
    digest_id: str
    run_id: str = "live"
    beat: str
    at: datetime
    version: str  # prompt/config hash
    entries: list[DigestEntry]
