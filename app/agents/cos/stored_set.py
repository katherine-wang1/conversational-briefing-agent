"""The per-user stored set (app/contracts/notes.py): the brief's stories first, then the next best by
the C5 ranking (`selection.rank_beyond`), up to `stored_set_size`. Pure code; no model calls.

Order: stories the spoken brief covers (in listening order), then stories C5 selected but C8 dropped
from the brief, then the rest. `note_status` says where a story's detail comes from: "shared" for
digest stories (a note is written once at newsroom time), "per_user" for the user's own finds
(`u:...` refs: no shared note; at most a light pack when `stored_set_extra_packs` > 0).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from app.agents.cos.pool import PoolItem
from app.agents.cos.selection import WHY_TAG, ScoredItem
from app.contracts.brief import BriefScript, BriefStory, CandidateScores, SelectedStory
from app.contracts.notes import StoredStory, UserStorySet

SUMMARY_CHARS = 400
MAX_URLS = 5


def note_status_for(ref: str) -> str:
    return "per_user" if ref.startswith("u:") else "shared"


def _story(
    ref: str,
    rank: int,
    item: PoolItem | None,
    score: CandidateScores | None,
    *,
    in_brief: bool,
    why: list[str],
    headline: str | None = None,
    beat: str | None = None,
) -> StoredStory:
    cand = item.candidate if item else None
    because = list(score.because[:3]) if score else []
    return StoredStory(
        story_ref=ref,
        rank=rank,
        headline=(cand.headline if cand else headline) or ref,
        summary=" ".join((cand.summary if cand else "").split())[:SUMMARY_CHARS],
        beat=beat if beat is not None else (item.beat if item else None),
        urls=list(dict.fromkeys(cand.urls))[:MAX_URLS] if cand else [],
        in_brief=in_brief,
        relevance=score.relevance if score else None,
        importance=float(score.importance) if score else (
            float(item.digest_importance) if item and item.digest_importance is not None else None
        ),
        why=[w for w in dict.fromkeys([*why, *because]) if w],
        note_status=note_status_for(ref),  # type: ignore[arg-type]
    )


def listening_order(script: BriefScript) -> list[BriefStory]:
    """The brief's stories in the order they are spoken (the writer may reorder `script.stories`)."""
    at: dict[str, int] = {}
    for i, sent in enumerate(script.sentences):
        if sent.story_ref and sent.story_ref not in at:
            at[sent.story_ref] = i
    return sorted(script.stories, key=lambda st: at.get(st.story_ref, len(script.sentences)))


def build_story_set(
    *,
    user_id: str,
    brief_id: str,
    local_date: str,
    created_at: datetime,
    brief_stories: Iterable[BriefStory],
    selected: Iterable[SelectedStory],
    extra: Iterable[tuple[ScoredItem, float]],
    pool: Mapping[str, PoolItem],
    scores: Mapping[str, CandidateScores],
    size: int,
    beats: Mapping[str, str | None] | None = None,
    extra_why: str = WHY_TAG["for_you"],
) -> UserStorySet:
    beats = beats or {}
    out: list[StoredStory] = []
    seen: set[str] = set()

    def add(s: StoredStory, *, force: bool = False) -> None:
        if s.story_ref in seen or (not force and len(out) >= max(size, 0)):
            return
        seen.add(s.story_ref)
        out.append(s)

    briefed = list(brief_stories)
    for st in briefed:
        if st.story_ref.startswith("followup:"):
            continue
        add(
            _story(
                st.story_ref, len(out) + 1, pool.get(st.story_ref), scores.get(st.story_ref),
                in_brief=True, why=[st.why_tag], headline=st.headline, beat=beats.get(st.story_ref),
            ),
            force=True,  # a `size` smaller than the brief still keeps the whole brief
        )
    # Ranked by C5 but cut from the spoken brief (e.g. every sentence failed the claim check).
    for sel in selected:
        add(
            _story(
                sel.story_ref, len(out) + 1, pool.get(sel.story_ref), scores.get(sel.story_ref),
                in_brief=False, why=[sel.why_tag, "cut from the brief"], beat=beats.get(sel.story_ref),
            )
        )
    for item, _score in extra:
        it = pool.get(item.story_ref)
        add(
            _story(
                item.story_ref, len(out) + 1, it, scores.get(item.story_ref),
                in_brief=False,
                why=[extra_why, it.origin if it else ""],
                beat=item.beat or beats.get(item.story_ref),
            )
        )
    return UserStorySet(
        user_id=user_id, brief_id=brief_id, local_date=local_date, stories=out, created_at=created_at
    )
