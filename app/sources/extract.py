"""Article body extraction (trafilatura), with hidden text removed first (TOOL-4).

Before extraction we drop elements a reader would not see: scripts, styles, templates, `hidden` /
`aria-hidden="true"` elements and inline-styled `display:none` / `visibility:hidden` / zero-size text.
After extraction zero-width and bidi-control characters are stripped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import lxml.html
import trafilatura

from app.sources.util import clean_text

EXTRACTOR_VER = f"trafilatura-{trafilatura.__version__}+hidden-strip-v1"
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(px|em|rem|%)?\s*(;|$)|opacity\s*:\s*0(\.0+)?\s*(;|$)",
    re.I,
)


@dataclass
class Extracted:
    text: str
    title: str | None
    date: str | None  # the page's own date (YYYY-MM-DD) if trafilatura found one
    canonical_url: str | None


def strip_hidden(html: str | bytes) -> str:
    doc = lxml.html.fromstring(html)
    for el in doc.xpath("//script|//style|//noscript|//template|//iframe|//svg"):
        el.drop_tree()
    for el in doc.xpath("//*[@hidden or @aria-hidden='true' or @style]"):
        if el.getparent() is None:
            continue
        if (
            el.get("hidden") is not None
            or el.get("aria-hidden") == "true"
            or _HIDDEN_STYLE.search(el.get("style") or "")
        ):
            el.drop_tree()
    return lxml.html.tostring(doc, encoding="unicode")


def extract_article(html: str | bytes, url: str | None = None) -> Extracted:
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    cleaned = strip_hidden(html) if html.strip() else ""
    text = (
        trafilatura.extract(
            cleaned,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            deduplicate=True,
        )
        if cleaned
        else None
    )
    meta = trafilatura.extract_metadata(html, default_url=url) if html.strip() else None
    return Extracted(
        text=clean_text(text or ""),
        title=(meta.title if meta else None),
        date=(meta.date if meta else None),
        canonical_url=(meta.url if meta else None),
    )
