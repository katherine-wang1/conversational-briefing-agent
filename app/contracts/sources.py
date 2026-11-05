"""Headline observations and documents.

Shared by live source adapters (app/sources) and archive collectors (eval/archive).
A replay backend must never return an object whose observed_at / available_at is after
the simulated "now" (TDD §5.2, RG-3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.contracts.common import Strict


class Engagement(Strict):
    """Engagement as observed at `observed_at`. Never a later value (no hindsight)."""

    hn_points: int | None = None
    comments: int | None = None
    upvotes: int | None = None  # reddit / HF papers likes, etc.


class HeadlineObservation(Strict):
    source_id: str  # matches config/beats/*.yaml headline_sources[].id
    beat: str  # beat id
    observed_at: datetime  # when this was visible (snapshot / fetch time)
    url: str
    title: str
    rank: int | None = None  # position on the front page / feed (1 = top)
    snippet: str | None = None
    engagement: Engagement | None = None
    published_at: datetime | None = None
    external_id: str | None = None  # e.g. HN item id, arXiv id
    # Additive (W-A). How this observation was obtained, e.g. "wayback" (as-of snapshot), "live"
    # (fetched now), "hindsight_listing" (a past-day listing fetched later: rank / engagement are NOT
    # as-of and live in `extra["hindsight"]`, never in `engagement`). None = unspecified (live adapters).
    provenance: str | None = None
    extra: dict[str, Any] | None = None  # source-specific extras (e.g. techmeme cluster size)


class Document(Strict):
    """An article / post / paper with extracted text. Keyed by url_hash in storage."""

    url_hash: str
    url: str
    canonical_url: str | None = None
    source: str | None = None
    title: str | None = None
    published_at: datetime | None = None
    available_at: datetime  # when it became observable (replay filter)
    fetched_at: datetime
    text: str = ""
    extractor_ver: str = "v0"
    # Additive (W-A). Where the body text came from: "wayback" (snapshot nearest after publish),
    # "origin_live" (fetched from the origin later; may be a later revision), "api" (arXiv / HF abstract).
    body_source: str | None = None


class SearchHit(Strict):
    doc_id: str  # url_hash
    url: str
    title: str
    source: str | None = None
    published_at: datetime | None = None
    snippet: str = Field(default="", max_length=600)


class Comment(Strict):
    item_id: str
    created_at: datetime
    text: str
    points: int | None = None
