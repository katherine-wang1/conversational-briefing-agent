"""RSS / Atom feeds (feedparser).

Two ways to turn a feed into observations:
* `as_snapshot=True` (a Wayback snapshot of the feed, or a live fetch): every entry is observed at the
  snapshot / fetch time, ranked by its position in the feed.
* `as_snapshot=False` (backfilling a window from a feed fetched *now*): every entry is observed at its own
  published time, unranked (`provenance="feed_pubdate"`). This trusts the feed's dates; entries without a
  date are dropped.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
import lxml.html

from app.contracts.sources import HeadlineObservation
from app.sources.util import normalize_url, one_line, to_utc


@dataclass
class FeedEntry:
    url: str
    title: str
    summary: str
    published_at: datetime | None
    updated_at: datetime | None
    guid: str | None
    position: int


def _struct_to_dt(st) -> datetime | None:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(st), tz=UTC)
    except (OverflowError, ValueError, TypeError):
        return None


def _html_to_text(s: str | None) -> str:
    if not s:
        return ""
    try:
        return one_line(lxml.html.fromstring(f"<div>{s}</div>").text_content())
    except Exception:  # noqa: BLE001 - malformed fragments: fall back to raw
        return one_line(s)


def parse_feed_entries(payload: str | bytes, base_url: str | None = None) -> list[FeedEntry]:
    fp = feedparser.parse(payload)
    out = []
    for pos, e in enumerate(fp.entries, start=1):
        link = e.get("link") or ""
        if not link:
            for lk in e.get("links", []):
                if lk.get("href"):
                    link = lk["href"]
                    break
        title = one_line(e.get("title"))
        if not link or not title:
            continue
        out.append(
            FeedEntry(
                url=normalize_url(link, base_url),
                title=title,
                summary=_html_to_text(e.get("summary"))[:1000],
                published_at=_struct_to_dt(e.get("published_parsed"))
                or _struct_to_dt(e.get("updated_parsed")),
                updated_at=_struct_to_dt(e.get("updated_parsed")),
                guid=e.get("id"),
                position=pos,
            )
        )
    return out


def parse_feed(
    payload: str | bytes,
    *,
    source_id: str,
    beat: str,
    observed_at: datetime | None = None,
    as_snapshot: bool = True,
    provenance: str | None = None,
    base_url: str | None = None,
) -> list[HeadlineObservation]:
    out = []
    for e in parse_feed_entries(payload, base_url):
        if as_snapshot:
            if observed_at is None:
                raise ValueError("observed_at is required for snapshot parsing")
            obs_at, rank, prov = to_utc(observed_at), e.position, provenance
        else:
            if e.published_at is None:
                continue
            obs_at, rank, prov = e.published_at, None, provenance or "feed_pubdate"
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=obs_at,
                url=e.url,
                title=e.title,
                rank=rank,
                snippet=e.summary or None,
                published_at=e.published_at,
                external_id=e.guid,
                provenance=prov,
                extra={"feed_position": e.position},
            )
        )
    return out
