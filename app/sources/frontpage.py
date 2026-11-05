"""Generic outlet / blog front pages: title + link + position.

Heuristics (no per-site rules): keep same-site links that look like articles (path deeper than the listing
page, not a known navigation path), whose text is long enough to be a headline. The title is the link's
heading element if it has one, otherwise its first text block. Rank = order of first appearance.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

import lxml.html

from app.contracts.sources import HeadlineObservation
from app.sources.util import normalize_url, one_line, to_utc

_NAV = re.compile(
    r"/(about|careers|jobs|contact|privacy|terms|legal|login|signin|signup|subscribe|newsletter|search|"
    r"tag|tags|category|categories|topics?|author|authors|page|events|podcasts?|videos?|feed|rss)(/|$)",
    re.I,
)
_SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|pdf|mp3|mp4|zip)$", re.I)


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlsplit(a).netloc.removeprefix("www."), urlsplit(b).netloc.removeprefix("www.")
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def _title_of(a) -> str:
    for tag in ("h1", "h2", "h3", "h4", "h5"):
        h = a.xpath(f".//{tag}")
        if h:
            t = one_line(h[0].text_content())
            if t:
                return t
    # first non-empty descendant text block
    for el in a.iter():
        if el is a:
            continue
        t = one_line(el.text or "")
        if len(t) >= 12:
            return t
    return one_line(a.text_content())


def parse_frontpage(
    html: str | bytes,
    *,
    page_url: str,
    observed_at: datetime,
    source_id: str,
    beat: str,
    provenance: str | None = None,
    min_words: int = 4,
    max_items: int = 60,
) -> list[HeadlineObservation]:
    doc = lxml.html.fromstring(html)
    for bad in doc.xpath("//script|//style|//noscript|//template|//nav|//footer|//header"):
        bad.drop_tree()
    base_path = urlsplit(page_url).path.rstrip("/")
    out: list[HeadlineObservation] = []
    seen: set[str] = set()
    for a in doc.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = normalize_url(href, page_url)
        if url in seen or not _same_site(url, page_url) or _SKIP_EXT.search(url):
            continue
        path = urlsplit(url).path.rstrip("/")
        if not path or path == base_path or _NAV.search(path):
            continue
        title = _title_of(a)
        if len(title.split()) < min_words:
            continue
        seen.add(url)
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=to_utc(observed_at),
                url=url,
                title=title[:300],
                rank=len(out) + 1,
                provenance=provenance,
            )
        )
        if len(out) >= max_items:
            break
    return out
