"""A ranked story list from an external ranker (Claude news agent, Event Registry), for the newsroom
comparison. Not a pipeline contract: the comparison maps these onto digest entries itself."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class RankedStory(BaseModel):
    rank: int  # 1 = most important
    headline: str
    summary: str = ""
    urls: list[str] = []
    outlets: list[str] = []
    importance: int = Field(default=50, ge=0, le=100)
    reason: str = ""
    extra: dict[str, Any] = {}  # provider-specific (e.g. Event Registry event_uri, article_count)

    @field_validator("importance", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> Any:
        if isinstance(v, int | float) and not isinstance(v, bool):
            return max(0, min(100, int(round(v))))
        return v


class RankedStories(list[RankedStory]):
    """A plain list of RankedStory, plus `usage` (cost / tokens; provider-specific) and `meta`."""

    def __init__(self, stories: Any = (), *, usage: Any = None, meta: dict[str, Any] | None = None):
        super().__init__(stories)
        self.usage = usage
        self.meta: dict[str, Any] = meta or {}
