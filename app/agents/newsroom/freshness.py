"""Freshness rule for a run's observations (reusable by any newsroom implementation).

Live feeds return their whole archive with `observed_at` = fetch time (openai_news gave 1,430 items,
hf_blog 918), so a window filter on `observed_at` alone lets months-old posts through as today's
news. This rule adds a check on the item's own date:

1. Observations from front-page sources (techmeme, hn_front, hf_papers, reddit_top, frontpage) are
   always kept, dated or not: being on the front page now is itself the signal (HF daily papers
   carry the arXiv date, often days before the listing; an HN post can link an old article).
2. Feed observations with `published_at` at or before the window start -> drop (`stale`).
3. Undated feed observations are kept only for the first `max_undated_per_source` per source, by
   feed order (`rank`, else `extra.feed_position`), per snapshot (`observed_at`).

Pure code, no I/O; the same rule applies in replay (archived observations in).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.contracts.sources import HeadlineObservation

FRONTPAGE_TYPES = frozenset({"techmeme", "hn_front", "hf_papers", "reddit_top", "frontpage"})


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _feed_order(o: HeadlineObservation) -> int:
    if o.rank is not None:
        return o.rank
    pos = (o.extra or {}).get("feed_position")
    return pos if isinstance(pos, int) else 10**6


def freshness_filter(
    observations: Sequence[HeadlineObservation],
    *,
    window_start: datetime,
    source_type: Callable[[str], str | None],
    max_undated_per_source: int = 20,
) -> tuple[list[HeadlineObservation], dict[str, int]]:
    """Returns (kept observations in input order, counts dropped per reason).

    `source_type(source_id)` gives the configured source type (None if unknown: treated as a feed).
    Reasons: `stale` (published before the window), `undated_over_cap` (undated feed item beyond the
    per-source cap).
    """
    ws = _utc(window_start)
    dropped = {"stale": 0, "undated_over_cap": 0}
    keep = [True] * len(observations)
    undated: dict[tuple[str, datetime], list[int]] = defaultdict(list)
    for i, o in enumerate(observations):
        if source_type(o.source_id) in FRONTPAGE_TYPES:
            continue
        if o.published_at is not None:
            if _utc(o.published_at) <= ws:
                keep[i] = False
                dropped["stale"] += 1
            continue
        undated[(o.source_id, _utc(o.observed_at))].append(i)
    for idx in undated.values():
        for i in sorted(idx, key=lambda i: (_feed_order(observations[i]), i))[max_undated_per_source:]:
            keep[i] = False
            dropped["undated_over_cap"] += 1
    return [o for o, k in zip(observations, keep, strict=True) if k], dropped
