"""Hacker News parsers.

* `parse_hn_front_html`: a front-page HTML page (live, a Wayback snapshot, or `/front?day=`), giving rank,
  points and comment counts *as shown on that page*. Whether those numbers are "as of" a past moment depends
  on where the HTML came from; the caller sets `provenance` accordingly.
* `parse_algolia_search`: HN Algolia `search` / `search_by_date` hits (story metadata, created_at).
* `parse_algolia_item`: HN Algolia `/items/{id}` comment trees (comments with created_at).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import lxml.html

from app.contracts.sources import Comment, Engagement, HeadlineObservation
from app.sources.util import clean_text, normalize_url, one_line, parse_iso, to_utc

HN_BASE = "https://news.ycombinator.com/"
_INT = re.compile(r"(\d[\d,]*)")


@dataclass
class HNFrontRow:
    item_id: str
    rank: int | None
    title: str
    url: str
    site: str | None
    points: int | None
    comments: int | None
    author: str | None
    created_at: datetime | None  # from the age tooltip, when the page has one


def _int(text: str | None) -> int | None:
    if not text:
        return None
    m = _INT.search(text)
    return int(m.group(1).replace(",", "")) if m else None


def parse_hn_front_rows(html: str | bytes) -> list[HNFrontRow]:
    doc = lxml.html.fromstring(html)
    rows: list[HNFrontRow] = []
    for tr in doc.xpath("//tr[contains(concat(' ', normalize-space(@class), ' '), ' athing ')]"):
        item_id = tr.get("id")
        if not item_id:
            continue
        if tr.xpath(".//td[contains(@class,'default')]"):  # comment rows on item pages
            continue
        rank_el = tr.xpath(".//span[@class='rank']")
        rank = _int(rank_el[0].text_content()) if rank_el else None
        a = tr.xpath(".//span[@class='titleline']/a[1]") or tr.xpath(
            ".//a[contains(@class,'storylink') or contains(@class,'titlelink')]"
        )
        if not a:
            continue
        a = a[0]
        href = a.get("href") or ""
        url = normalize_url(href, HN_BASE)
        site_el = tr.xpath(".//span[@class='sitestr']")
        sub = tr.getnext()
        points = comments = None
        author = None
        created = None
        if sub is not None:
            sc = sub.xpath(".//span[@class='score']")
            points = _int(sc[0].text_content()) if sc else None
            au = sub.xpath(".//a[@class='hnuser']")
            author = au[0].text_content().strip() if au else None
            age = sub.xpath(".//span[@class='age']")
            if age:
                title_attr = (age[0].get("title") or "").split(" ")[0]
                created = parse_iso(title_attr) if title_attr else None
            for link in sub.xpath(".//a"):
                t = link.text_content().replace("\xa0", " ").strip().lower()
                if t.endswith("comments") or t.endswith("comment"):
                    comments = _int(t)
                elif t == "discuss":
                    comments = 0
        rows.append(
            HNFrontRow(
                item_id=item_id,
                rank=rank,
                title=one_line(a.text_content()),
                url=url,
                site=site_el[0].text_content().strip() if site_el else None,
                points=points,
                comments=comments,
                author=author,
                created_at=created,
            )
        )
    return rows


def parse_hn_front_html(
    html: str | bytes,
    *,
    observed_at: datetime,
    source_id: str = "hn_front",
    beat: str = "ai",
    provenance: str | None = None,
    engagement_is_as_of: bool = True,
    listing_day: str | None = None,
) -> list[HeadlineObservation]:
    """Front-page rows -> observations.

    If `engagement_is_as_of` is False (e.g. `/front?day=` fetched weeks later), points, comments and rank go
    to `extra["hindsight"]` and the as-of fields (`rank`, `engagement`) are left empty.
    """
    out = []
    for r in parse_hn_front_rows(html):
        extra: dict[str, Any] = {"hn_item_id": r.item_id, "site": r.site, "author": r.author}
        if listing_day:
            extra["listing_day"] = listing_day
        shown = {"rank": r.rank, "hn_points": r.points, "comments": r.comments}
        if engagement_is_as_of:
            rank, eng = r.rank, Engagement(hn_points=r.points, comments=r.comments)
        else:
            rank, eng = None, None
            extra["hindsight"] = shown
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=to_utc(observed_at),
                url=r.url,
                title=r.title,
                rank=rank,
                engagement=eng,
                published_at=r.created_at,
                external_id=r.item_id,
                provenance=provenance,
                extra=extra,
            )
        )
    return out


def parse_hn_more_link(html: str | bytes) -> str | None:
    doc = lxml.html.fromstring(html)
    a = doc.xpath("//a[contains(@class,'morelink')]")
    return normalize_url(a[0].get("href"), HN_BASE) if a else None


# ---------------------------------------------------------------- Algolia


@dataclass
class HNStory:
    item_id: str
    created_at: datetime
    title: str
    url: str
    author: str | None
    points: int | None  # value at fetch time: hindsight, labels only
    num_comments: int | None  # value at fetch time: hindsight, labels only
    tags: list[str]


def parse_algolia_search(payload: str | bytes | dict) -> tuple[list[HNStory], dict[str, Any]]:
    """Returns (stories, page_info) where page_info has nbHits, nbPages, page, hitsPerPage."""
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    stories = []
    for h in data.get("hits", []):
        created = parse_iso(h.get("created_at"))
        title = one_line(h.get("title") or h.get("story_title") or "")
        if created is None or not title:
            continue
        item_id = str(h.get("objectID") or h.get("story_id"))
        url = h.get("url") or f"{HN_BASE}item?id={item_id}"
        stories.append(
            HNStory(
                item_id=item_id,
                created_at=created,
                title=title,
                url=normalize_url(url),
                author=h.get("author"),
                points=h.get("points"),
                num_comments=h.get("num_comments"),
                tags=list(h.get("_tags") or []),
            )
        )
    info = {k: data.get(k) for k in ("nbHits", "nbPages", "page", "hitsPerPage")}
    return stories, info


def story_to_observation(s: HNStory, *, fetched_at: datetime, beat: str = "ai") -> HeadlineObservation:
    """An Algolia story as an observation at its submission time (it was visible on /newest then).

    No as-of engagement is known at created_at, so `engagement` is None; the values seen at fetch time
    are kept in `extra["hindsight"]` (labels only).
    """
    return HeadlineObservation(
        source_id="hn_algolia",
        beat=beat,
        observed_at=s.created_at,
        url=s.url,
        title=s.title,
        published_at=s.created_at,
        external_id=s.item_id,
        provenance="api_created_at",
        extra={
            "author": s.author,
            "hindsight": {
                "hn_points": s.points,
                "comments": s.num_comments,
                "fetched_at": to_utc(fetched_at).isoformat(),
            },
        },
    )


@dataclass
class HNComment:
    comment: Comment
    story_id: str
    parent_id: str | None
    author: str | None


def parse_algolia_item(payload: str | bytes | dict) -> list[HNComment]:
    """Flatten an Algolia item tree into comments (depth-first, deleted/empty comments skipped)."""
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    story_id = str(data.get("story_id") or data.get("id"))
    out: list[HNComment] = []

    def walk(node: dict, parent: str | None) -> None:
        for ch in node.get("children") or []:
            if ch.get("type") == "comment" and ch.get("text"):
                created = parse_iso(ch.get("created_at"))
                if created is not None:
                    raw = ch["text"].replace("<p>", "\n\n<p>")
                    text = clean_text(lxml.html.fromstring(f"<div>{raw}</div>").text_content())
                    out.append(
                        HNComment(
                            comment=Comment(
                                item_id=str(ch["id"]), created_at=created, text=text, points=ch.get("points")
                            ),
                            story_id=story_id,
                            parent_id=parent,
                            author=ch.get("author"),
                        )
                    )
            walk(ch, str(ch.get("id")))

    walk(data, story_id)
    return out
