"""T1 code graders (TDD §18.2): importance recall@K,d, relevance, topic coverage, latent discovery."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta

from pydantic import BaseModel

from eval.graders.mathutil import entropy, mean
from eval.graders.records import ShownStory
from eval.sim.profile import HiddenInterest, LatentInterest, SimProfile

# ---- term matching -----------------------------------------------------------------------------


def term_in(term: str, text: str) -> bool:
    """Case-insensitive match of `term` in `text` on alphanumeric boundaries ('AMD' not in 'AMDAHL')."""
    if not term.strip():
        return False
    pat = r"(?<![0-9a-z])" + re.escape(term.lower()) + r"(?![0-9a-z])"
    return re.search(pat, text.lower()) is not None


def story_has_term(story: ShownStory, term: str) -> bool:
    return any(term.lower() == e.lower() for e in story.entities) or term_in(term, story.text or story.headline)


# ---- importance recall -------------------------------------------------------------------------


class RecallResult(BaseModel):
    per_user: dict[str, float]
    mean: float | None
    n_important: int
    d: int
    k: int | None


def importance_recall(
    important_first_seen: Mapping[str, date],
    shown: Iterable[ShownStory],
    users: Sequence[str],
    *,
    d: int = 2,
    k: int | None = None,
    window: tuple[date, date] | None = None,
) -> RecallResult:
    """Share of hindsight-important stories that appeared in a user's brief within `d` days of
    `first_seen` (0 <= day - first_seen <= d), averaged over users.

    `important_first_seen`: label story id -> first_seen date (coverage label, TDD §15.4).
    `window`: only stories first seen in [start, end] count. `k`: only the first k stories of each
    brief count (recall@K). Users with no briefs still count (recall 0) - pass them in `users`.
    """
    imp = {s: fs for s, fs in important_first_seen.items()
           if window is None or window[0] <= fs <= window[1]}
    hits: dict[str, set[str]] = defaultdict(set)
    for s in shown:
        sid = s.label_story_id or s.story_ref
        if sid not in imp or (k is not None and s.rank >= k):
            continue
        lag = (s.day - imp[sid]).days
        if 0 <= lag <= d:
            hits[s.user_id].add(sid)
    per_user = {u: (len(hits[u]) / len(imp) if imp else 0.0) for u in users}
    return RecallResult(per_user=per_user, mean=mean(per_user.values()) if imp else None,
                        n_important=len(imp), d=d, k=k)


def relative_to_baseline(value: float | None, baseline: float | None) -> float | None:
    """Recall reported relative to B1 (importance-only): value / baseline."""
    if value is None or not baseline:
        return None
    return value / baseline


# ---- relevance (primary, code) -----------------------------------------------------------------


def match_strength(story: ShownStory, interest: HiddenInterest) -> float:
    """m(s,k) = min(1, 1[subtopic matches] + 0.5 * 1[an entity or keyword matches])."""
    m = 1.0 if interest.subtopic in story.subtopics else 0.0
    if any(story_has_term(story, t) for t in (*interest.entities, *interest.keywords)):
        m += 0.5
    return min(1.0, m)


def relevance(story: ShownStory, interests: Sequence[HiddenInterest]) -> float:
    """rel(s,u,t) = sum_k w_k(t) * m(s,k) with hidden weights at day t (they sum to 1, so rel in [0,1])."""
    return sum(i.weight * match_strength(story, i) for i in interests)


def mean_relevance(shown: Iterable[ShownStory], profiles: Mapping[str, SimProfile]) -> dict[str, float | None]:
    """Mean relevance over shown stories, per user."""
    per: dict[str, list[float]] = defaultdict(list)
    for s in shown:
        p = profiles[s.user_id]
        per[s.user_id].append(relevance(s, p.interests_at(s.day_index)))
    return {u: mean(v) for u, v in per.items()}


# ---- topic coverage ----------------------------------------------------------------------------


class CoverageResult(BaseModel):
    windows: list[dict]  # [{user, start, distinct, entropy, n_stories}]
    mean_distinct: float | None
    mean_entropy: float | None


def topic_coverage(shown: Iterable[ShownStory], window_start: date, window_days: int = 7) -> CoverageResult:
    """Distinct subtopics and Shannon entropy (bits) of shown subtopics per user per non-overlapping
    7-day window from `window_start`. A story with several subtopics splits its unit mass evenly."""
    buckets: dict[tuple[str, int], list[ShownStory]] = defaultdict(list)
    for s in shown:
        buckets[(s.user_id, (s.day - window_start).days // window_days)].append(s)
    rows = []
    for (u, w), stories in sorted(buckets.items()):
        mass: dict[str, float] = defaultdict(float)
        for s in stories:
            for sub in s.subtopics:
                mass[sub] += 1 / len(s.subtopics)
        rows.append({"user": u, "start": (window_start + timedelta(days=w * window_days)).isoformat(),
                     "distinct": len(mass), "entropy": entropy(mass), "n_stories": len(stories)})
    return CoverageResult(windows=rows, mean_distinct=mean(r["distinct"] for r in rows),
                          mean_entropy=mean(r["entropy"] for r in rows))


# ---- latent discovery --------------------------------------------------------------------------


def latent_matches(story: ShownStory, li: LatentInterest) -> bool:
    """A story surfaces a latent interest if it matches one of its terms; a latent interest with no
    terms is matched by subtopic."""
    if li.terms:
        return any(story_has_term(story, t) for t in li.terms)
    return li.subtopic in story.subtopics


class LatentResult(BaseModel):
    per_user_surfaced_share: dict[str, float]
    mean_surfaced_share: float | None
    surfaced_events: int
    heard_events: int
    engaged_events: int
    engagement_rate: float | None  # engaged / heard surfacing events


def latent_discovery(shown: Iterable[ShownStory], profiles: Mapping[str, SimProfile]) -> LatentResult:
    shown = list(shown)
    by_user: dict[str, list[ShownStory]] = defaultdict(list)
    for s in shown:
        by_user[s.user_id].append(s)
    share: dict[str, float] = {}
    surfaced = heard = engaged = 0
    for u, stories in by_user.items():
        lat = profiles[u].latent_interests
        if not lat:
            continue
        hit = set()
        for s in stories:
            matched = [i for i, li in enumerate(lat) if latent_matches(s, li)]
            if not matched:
                continue
            hit.update(matched)
            surfaced += 1
            if s.heard != "none":
                heard += 1
                engaged += int(s.engaged)
        share[u] = len(hit) / len(lat)
    return LatentResult(per_user_surfaced_share=share, mean_surfaced_share=mean(share.values()),
                        surfaced_events=surfaced, heard_events=heard, engaged_events=engaged,
                        engagement_rate=engaged / heard if heard else None)
