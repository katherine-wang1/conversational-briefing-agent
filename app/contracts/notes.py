"""Story notes and the per-user stored set (docs/architecture-revisit.md, part 2).

A story note is written once per shared digest story, at newsroom time, and is read by every user's
run and by the voice agent: facts with citations, who is involved, context and why it matters. It is
built only from fetched source text (never from the model's background knowledge for news claims).

The per-user stored set is the larger ranked list each user's daily run keeps (default 20, across
beats): the 4–6 stories the spoken brief covers plus the rest the user may ask about live.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.contracts.common import Strict


class NoteSource(Strict):
    """One source the note writer tried. `url` is what was fetched (a publisher URL after resolving
    Google News links); `listed_url` is the URL as the digest listed it, if different."""

    url: str
    listed_url: str | None = None
    outlet: str | None = None
    status: Literal["ok", "paywalled", "failed", "skipped"] = "ok"
    error: str | None = None


class NoteFact(Strict):
    text: str  # one factual sentence
    source_urls: list[str]  # each must be a NoteSource.url with status "ok"


class StoryNote(Strict):
    story_id: str  # DigestEntry.story_id
    digest_id: str
    beat: str
    headline: str
    facts: list[NoteFact] = []  # what happened, cited
    entities: list[str] = []  # people, companies, products, agencies involved
    context: str = ""  # background a listener needs; may use stable background knowledge
    why_it_matters: str = ""
    open_questions: list[str] = []  # what isn't known yet
    sources: list[NoteSource] = []
    status: Literal["ok", "partial", "no_text"] = "ok"  # no_text: no source could be fetched
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime


class StoredStory(Strict):
    """One story in a user's stored set."""

    story_ref: str  # pool ref: a digest story_id, or a per-user find ("u:...")
    rank: int = Field(ge=1)
    headline: str
    summary: str = ""
    beat: str | None = None
    urls: list[str] = []
    in_brief: bool = False  # covered by the spoken brief
    relevance: float | None = None  # the user's score for it (C4)
    importance: float | None = None
    why: list[str] = []  # short reasons / memory refs
    note_status: Literal["shared", "per_user", "none"] = "none"  # where its note comes from


class UserStorySet(Strict):
    user_id: str
    brief_id: str
    local_date: str
    stories: list[StoredStory]
    created_at: datetime
