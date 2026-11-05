"""Techmeme parsers.

* `parse_techmeme_front`: the front page (live `https://www.techmeme.com/`, a Wayback snapshot of it, or a
  dated archive page `/YYMMDD/hHHMM`; all share one layout). One observation per story cluster, ranked in
  page order within the ranked sections (Top News, then More News). Cluster size (number of "More:" links)
  and the permalink id go to `extra`.
* `parse_techmeme_river`: `/river`, the reverse-chronological list of every headline Techmeme posted in the
  last few days, with its post time (US Eastern). The river ignores `?date=`: it only ever shows the most
  recent ~5 days (checked 2026-11-05).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import lxml.html

from app.contracts.sources import HeadlineObservation
from app.sources.util import EASTERN, normalize_url, one_line, to_utc

TM_BASE = "https://www.techmeme.com/"
RANKED_SECTIONS = ("topcol1", "topcol2", "topcol3", "botcol1")  # Top News columns, then More News
_SECTION_NAMES = {
    "topcol1": "top",
    "topcol2": "top",
    "topcol3": "top",
    "botcol1": "more",
    "botcol2": "earlier",
}


def _section(el) -> str | None:
    for anc in el.iterancestors("div"):
        sid = anc.get("id")
        if sid in _SECTION_NAMES:
            return sid
    return None


def _cite_text(item) -> str | None:
    c = item.xpath(".//cite")
    if not c:
        return None
    return one_line(c[0].text_content()).rstrip(":").strip() or None


def parse_techmeme_front(
    html: str | bytes,
    *,
    observed_at: datetime,
    source_id: str = "techmeme",
    beat: str = "ai",
    provenance: str | None = None,
    include_earlier: bool = False,
) -> list[HeadlineObservation]:
    doc = lxml.html.fromstring(html)
    sections = RANKED_SECTIONS + (("botcol2",) if include_earlier else ())
    out: list[HeadlineObservation] = []
    seen: set[str] = set()
    rank = 0
    for clus in doc.xpath("//div[@class='clus']"):
        sec = _section(clus)
        if sec not in sections:
            continue
        lead = clus.xpath(".//a[contains(concat(' ', normalize-space(@class), ' '), ' ourh ')]")
        if not lead:
            continue
        a = lead[0]
        url = normalize_url(a.get("href") or "", TM_BASE)
        if url in seen:
            continue
        seen.add(url)
        rank += 1
        item = a.xpath("ancestor::div[@class='item'][1]")
        item = item[0] if item else clus
        snippet = None
        ii = a.xpath("ancestor::div[@class='ii'][1]")
        if ii:
            full = one_line(ii[0].text_content())
            title = one_line(a.text_content())
            rest = full.split(title, 1)[-1] if title in full else ""
            snippet = rest.lstrip(" —-").strip() or None
        itc2 = clus.xpath(".//div[@class='itc2']")
        permalink = itc2[0].get("id") if itc2 else None
        related = []
        for d in clus.xpath(".//div[@class='di']"):
            links = d.xpath("./a[@href]")
            if links:
                related.append(normalize_url(links[-1].get("href"), TM_BASE))
        extra: dict[str, Any] = {
            "section": _SECTION_NAMES.get(sec or "", sec),
            "cite": _cite_text(item),
            "permalink_id": permalink,
            "related_count": len(related),
            "related_domains": sorted({u.split("/")[2] for u in related}),
        }
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=to_utc(observed_at),
                url=url,
                title=one_line(a.text_content()),
                rank=rank,
                snippet=snippet,
                provenance=provenance,
                extra=extra,
            )
        )
    return out


def parse_techmeme_page_time(html: str | bytes) -> datetime | None:
    """The page's own timestamp (`#thisdate`, US Eastern), e.g. 'November 5, 2026, 2:30 AM'."""
    doc = lxml.html.fromstring(html)
    el = doc.xpath("//div[@id='thisdate']")
    if not el:
        return None
    txt = one_line(el[0].text_content())
    try:
        return to_utc(datetime.strptime(txt, "%B %d, %Y, %I:%M %p").replace(tzinfo=EASTERN))
    except ValueError:
        return None


_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)


def parse_techmeme_river(
    html: str | bytes,
    *,
    source_id: str = "techmeme_river",
    beat: str = "ai",
    provenance: str = "river_post_time",
) -> list[HeadlineObservation]:
    """Each river row -> an observation at its Techmeme post time (converted from US Eastern to UTC).

    `rank` is left empty: the river is chronological, not ranked.
    """
    doc = lxml.html.fromstring(html)
    col = doc.xpath("//div[@id='countercol']")
    if not col:
        return []
    out: list[HeadlineObservation] = []
    day: datetime | None = None
    for el in col[0].iter("h2", "tr"):
        if el.tag == "h2":
            try:
                day = datetime.strptime(one_line(el.text_content()), "%B %d, %Y")
            except ValueError:
                day = None  # e.g. "Sponsor Posts": stop attributing rows to a date
            continue
        if day is None or "ritem" not in (el.get("class") or ""):
            continue
        tds = el.xpath("./td")
        if len(tds) < 2:
            continue
        m = _TIME.search(tds[0].text_content())
        if not m:
            continue
        hh, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        hh = hh % 12 + (12 if ap == "PM" else 0)
        posted = to_utc(day.replace(hour=hh, minute=mm, tzinfo=EASTERN))
        links = [a for a in tds[1].xpath("./a") if a.get("href")]
        if not links:
            continue
        a = links[-1]
        shr = tds[1].xpath(".//div[@class='rshr']")
        extra = {
            "cite": _cite_text(tds[1]),
            "permalink_id": shr[0].get("pml") if shr else None,
            "posted_local": f"{day.date().isoformat()} {hh:02d}:{mm:02d} America/New_York",
        }
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=posted,
                url=normalize_url(a.get("href"), TM_BASE),
                title=one_line(a.text_content()),
                published_at=None,
                provenance=provenance,
                extra=extra,
            )
        )
    return out


def is_cloudflare_challenge(status: int, body: bytes | str, headers: dict[str, str] | None = None) -> bool:
    if headers and headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if status not in (403, 429, 503):
        return False
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    markers = (
        "Just a moment",
        "challenge-platform",
        "cf_chl",
        "Attention Required! | Cloudflare",
        "cf-error-details",
    )
    return any(m in text for m in markers)
