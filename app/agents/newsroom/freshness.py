"""Freshness rule for a run's observations (reusable by any newsroom implementation).

Live feeds return their whole archive with `observed_at` = fetch time (openai_news gave 1,430 items,
hf_blog 918), so a window filter on `observed_at` alone lets months-old posts through as today's
news. This rule adds a check on the item's own date:

1. Observations from aggregator front pages (techmeme, hn_front, hf_papers, reddit_top:
   `EXEMPT_TYPES`) are always kept, dated or not: being on the aggregator's front page now is itself
   the signal (HF daily papers carry the arXiv date, often days before the listing; an HN post can
   link an old article). A single outlet's or lab's own index page (`frontpage` type, e.g.
   anthropic_news, meta_ai_blog) is not exempt: it lists its posts whether or not they are new.
2. Observations with `published_at` at or before the window start -> drop (`stale`).
3. Undated observations from a page whose newest shown date (`extra.page_newest_date`, set by the
   `frontpage` parser) is at or before the window start -> drop (`stale_page`): nothing on that page
   is new.
4. Other undated observations are kept only for the first `max_undated_per_source` per source, by
   feed order (`rank`, else `extra.feed_position`), per snapshot (`observed_at`).

Pure code, no I/O; the same rule applies in replay (archived observations in).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.contracts.sources import HeadlineObservation

# Sources whose listing is itself the "it matters now" signal (the editor also uses this set to weight
# candidates by front-page position).
FRONTPAGE_TYPES = frozenset({"techmeme", "hn_front", "hf_papers", "reddit_top", "frontpage"})
# Exempt from the date rule: aggregator front pages only, not a single site's own index page.
EXEMPT_TYPES = frozenset({"techmeme", "hn_front", "hf_papers", "reddit_top"})


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _feed_order(o: HeadlineObservation) -> int:
    if o.rank is not None:
        return o.rank
    pos = (o.extra or {}).get("feed_position")
    return pos if isinstance(pos, int) else 10**6


def _page_newest(o: HeadlineObservation) -> datetime | None:
    v = (o.extra or {}).get("page_newest_date")
    if not v:
        return None
    try:
        return _utc(v if isinstance(v, datetime) else datetime.fromisoformat(str(v)))
    except ValueError:
        return None


def freshness_filter(
    observations: Sequence[HeadlineObservation],
    *,
    window_start: datetime,
    source_type: Callable[[str], str | None],
    max_undated_per_source: int = 20,
) -> tuple[list[HeadlineObservation], dict[str, int]]:
    """Returns (kept observations in input order, counts dropped per reason).

    `source_type(source_id)` gives the configured source type (None if unknown: treated as a feed).
    Reasons: `stale` (published before the window), `stale_page` (undated, on a page whose newest
    date is before the window), `undated_over_cap` (undated item beyond the per-source cap).
    """
    ws = _utc(window_start)
    dropped = {"stale": 0, "stale_page": 0, "undated_over_cap": 0}
    keep = [True] * len(observations)
    undated: dict[tuple[str, datetime], list[int]] = defaultdict(list)
    for i, o in enumerate(observations):
        if source_type(o.source_id) in EXEMPT_TYPES:
            continue
        if o.published_at is not None:
            if _utc(o.published_at) <= ws:
                keep[i] = False
                dropped["stale"] += 1
            continue
        newest = _page_newest(o)
        if newest is not None and newest <= ws:
            keep[i] = False
            dropped["stale_page"] += 1
            continue
        undated[(o.source_id, _utc(o.observed_at))].append(i)
    for idx in undated.values():
        for i in sorted(idx, key=lambda i: (_feed_order(observations[i]), i))[max_undated_per_source:]:
            keep[i] = False
            dropped["undated_over_cap"] += 1
    return [o for o, k in zip(observations, keep, strict=True) if k], dropped
