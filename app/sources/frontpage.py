"""Generic outlet / blog front pages: title + link + position.

Heuristics (no per-site rules): keep same-site links that look like articles (path deeper than the listing
page, not a known navigation path), whose text is long enough to be a headline. The title is the link's
heading element if it has one, otherwise its first text block. Rank = order of first appearance.

Dates (generic, for the newsroom freshness rule): `published_at` is set when one of the link's cards
(the link or an ancestor up to 3 levels that holds no other article link; every appearance of the link
is checked, since a featured grid often repeats a dated list) carries a `<time>` element or a date in
text ("Nov 20, 2026", "2026-11-20", "20 November 2026"). Dates before 2000 or more than a day after
the fetch (event and webinar listings) are ignored. Day-only dates become the end of that day, capped
at the fetch time, so a post is never dated earlier than it could be. When at least half of the
page's stories (and at least 3) are dated this way, every observation also gets
`extra.page_newest_date` = the newest story date: a page that dates its stories and shows nothing
newer than that has nothing newer.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
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
_MON = "(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\.?"
_DATE = re.compile(
    rf"\b(?:{_MON}\s+(\d{{1,2}}),?\s+(\d{{4}})|(\d{{4}})-(\d{{2}})-(\d{{2}})|(\d{{1,2}})\s+{_MON}\s+(\d{{4}}))\b",
    re.I,
)
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _dates_in(text: str) -> list[datetime]:
    out = []
    for m in _DATE.finditer(text):
        g = m.groups()
        try:
            if g[0]:
                y, mo, d = int(g[2]), _MONTHS[g[0][:3].lower()], int(g[1])
            elif g[3]:
                y, mo, d = int(g[3]), int(g[4]), int(g[5])
            else:
                y, mo, d = int(g[8]), _MONTHS[g[7][:3].lower()], int(g[6])
            out.append(datetime.combine(datetime(y, mo, d).date(), time(23, 59, 59), UTC))
        except (ValueError, KeyError):
            continue
    return out


def _time_el_dates(el) -> list[datetime]:
    out = []
    for t in el.xpath(".//time") if el.tag != "time" else [el]:
        v = (t.get("datetime") or "").strip()
        if v:
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if len(v) <= 10:  # date only
                    dt = datetime.combine(dt.date(), time(23, 59, 59), UTC)
                out.append(to_utc(dt))
                continue
            except ValueError:
                pass
        out += _dates_in(" ".join(t.itertext()))
    return out


def _plausible(dts: list[datetime], fetched: datetime) -> list[datetime]:
    return [min(d, fetched) for d in dts if d.year >= 2000 and d <= fetched + timedelta(days=1)]


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
    fetched = to_utc(observed_at)

    def is_article(url: str) -> bool:
        if not _same_site(url, page_url) or _SKIP_EXT.search(url):
            return False
        path = urlsplit(url).path.rstrip("/")
        return bool(path) and path != base_path and not _NAV.search(path)

    links: list[tuple[object, str]] = []  # every article-like link, in document order
    out: list[HeadlineObservation] = []
    for a in doc.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = normalize_url(href, page_url)
        if not is_article(url):
            continue
        links.append((a, url))
        if len(out) >= max_items or any(o.url == url for o in out):
            continue
        title = _title_of(a)
        if len(title.split()) < min_words:
            continue
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=fetched,
                url=url,
                title=title[:300],
                rank=len(out) + 1,
                provenance=provenance,
            )
        )

    stories = {o.url for o in out}

    def card_date(a, url: str) -> datetime | None:
        el = a
        for _ in range(4):  # the link itself, then up to 3 ancestors
            hrefs = el.xpath(".//a/@href") if el is not a else []
            if any(normalize_url(h, page_url) in stories - {url} for h in hrefs):
                return None  # left the card: this element holds another story
            dts = _plausible(_time_el_dates(el) or _dates_in(" ".join(el.itertext())), fetched)
            if dts:
                return max(dts)
            el = el.getparent()
            if el is None:
                return None
        return None

    dates: dict[str, datetime] = {}
    for a, url in links:
        if url in stories and url not in dates and (d := card_date(a, url)) is not None:
            dates[url] = d
    dated = [dates[o.url] for o in out if o.url in dates]
    newest = max(dated) if len(dated) >= 3 and 2 * len(dated) >= len(out) else None
    return [
        o.model_copy(
            update={
                "published_at": dates.get(o.url),
                "extra": {"page_newest_date": newest.isoformat()} if newest else None,
            }
        )
        for o in out
    ]
