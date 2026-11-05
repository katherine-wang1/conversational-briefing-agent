"""arXiv API (Atom) listings.

`observed_at` is an estimate of when the paper was first *announced* (listed publicly), not its submission
time: arXiv announces at 20:00 US Eastern, Sunday to Thursday, the submissions received before the 14:00 ET
cutoff of the preceding weekday. A paper submitted after Thursday's cutoff is announced on Sunday evening.
Holidays are not modelled (they only delay announcements, so the estimate errs early by those days).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

import feedparser

from app.contracts.sources import HeadlineObservation
from app.sources.util import EASTERN, one_line, parse_iso, to_utc

ARXIV_API = "https://export.arxiv.org/api/query"


def announce_time(submitted: datetime) -> datetime:
    """Estimated public announcement time (UTC) for a v1 submitted at `submitted`."""
    local = to_utc(submitted).astimezone(EASTERN)
    d = local.date()
    # the first weekday cutoff (Mon-Fri 14:00 ET) after submission
    if d.weekday() >= 5 or local.time() >= time(14, 0):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    # cutoff on Mon-Thu -> announced 20:00 same day; Friday cutoff -> Sunday 20:00
    ann_day = d + timedelta(days=2) if d.weekday() == 4 else d
    return to_utc(datetime.combine(ann_day, time(20, 0), tzinfo=EASTERN))


@dataclass
class ArxivEntry:
    arxiv_id: str  # without version, e.g. 2610.00850
    version: str
    title: str
    summary: str
    published: datetime  # v1 submission time
    updated: datetime | None
    primary_category: str | None
    categories: list[str]
    authors: list[str]
    abs_url: str
    comment: str | None


def parse_arxiv_feed(payload: str | bytes) -> tuple[list[ArxivEntry], dict[str, Any]]:
    """Returns (entries, info) where info has totalResults, startIndex, itemsPerPage."""
    fp = feedparser.parse(payload)
    out = []
    for e in fp.entries:
        raw_id = e.get("id", "")
        tail = raw_id.rsplit("/abs/", 1)[-1]
        base, _, ver = tail.rpartition("v")
        if not base or not ver.isdigit():
            base, ver = tail, ""
        published = parse_iso(e.get("published"))
        if not base or published is None:
            continue
        cats = [t.get("term") for t in e.get("tags", []) if t.get("term")]
        prim = e.get("arxiv_primary_category", {}).get("term") if e.get("arxiv_primary_category") else None
        out.append(
            ArxivEntry(
                arxiv_id=base,
                version=f"v{ver}" if ver else "",
                title=one_line(e.get("title")),
                summary=one_line(e.get("summary")),
                published=published,
                updated=parse_iso(e.get("updated")),
                primary_category=prim,
                categories=cats,
                authors=[a.get("name", "") for a in e.get("authors", [])],
                abs_url=f"https://arxiv.org/abs/{base}",
                comment=one_line(e.get("arxiv_comment")) or None,
            )
        )
    feed = fp.get("feed", {})
    info = {
        "totalResults": int(feed.get("opensearch_totalresults", 0) or 0),
        "startIndex": int(feed.get("opensearch_startindex", 0) or 0),
        "itemsPerPage": int(feed.get("opensearch_itemsperpage", 0) or 0),
    }
    return out, info


def entry_to_observation(
    e: ArxivEntry, *, source_id: str, beat: str = "ai", rank: int | None = None
) -> HeadlineObservation:
    return HeadlineObservation(
        source_id=source_id,
        beat=beat,
        observed_at=announce_time(e.published),
        url=e.abs_url,
        title=e.title,
        rank=rank,
        snippet=e.summary[:600] or None,
        published_at=e.published,
        external_id=e.arxiv_id,
        provenance="api_announce_estimate",
        extra={
            "primary_category": e.primary_category,
            "categories": e.categories,
            "authors": e.authors[:20],
            "n_authors": len(e.authors),
            "comment": e.comment,
            "observed_at_rule": "arxiv announcement estimate (20:00 ET, 14:00 ET cutoff, no holidays)",
        },
    )


def query_url(
    cat: str, day_start: datetime, day_end: datetime, start: int = 0, max_results: int = 1000
) -> str:
    """submittedDate query for [day_start, day_end] (UTC, minute precision), ascending."""
    a = to_utc(day_start).strftime("%Y%m%d%H%M")
    b = to_utc(day_end).strftime("%Y%m%d%H%M")
    return (
        f"{ARXIV_API}?search_query=cat:{cat}+AND+submittedDate:%5B{a}+TO+{b}%5D"
        f"&start={start}&max_results={max_results}&sortBy=submittedDate&sortOrder=ascending"
    )
