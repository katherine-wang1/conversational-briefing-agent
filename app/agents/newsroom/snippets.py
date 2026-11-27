"""Publisher snippets for a digest story (N6): the headline and summary paragraph each outlet itself put in
its feed (RSS description, Techmeme or front-page excerpt), verbatim, so a story whose articles can't be
fetched (paywall, bot block) can still be told at summary level, attributed to the outlet.

Pure code, no I/O. Snippets that only repeat the headline (Google News descriptions are "<title> <outlet>")
are dropped; discussion sources (Hacker News, Reddit) are not publishers and are skipped.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from app.agents.newsroom.normalize import NormItem, clean_text, registrable_domain
from app.contracts.digest import SourceSnippet

MAX_SNIPPETS = 5
MIN_SNIPPET_CHARS = 40  # what's left of a snippet once its headline and outlet are removed
NON_PUBLISHER_TYPES = frozenset({"hn_front", "reddit_top"})

# How a listener hears the outlets we see most (registrable domain -> name). Others: the feed's own outlet
# name, else the domain.
OUTLET_NAMES = {
    "nytimes.com": "The New York Times",
    "wsj.com": "The Wall Street Journal",
    "ft.com": "the Financial Times",
    "bloomberg.com": "Bloomberg",
    "reuters.com": "Reuters",
    "theinformation.com": "The Information",
    "economist.com": "The Economist",
    "washingtonpost.com": "The Washington Post",
    "barrons.com": "Barron's",
    "nikkei.com": "Nikkei Asia",
    "axios.com": "Axios",
    "businessinsider.com": "Business Insider",
    "theatlantic.com": "The Atlantic",
    "wired.com": "Wired",
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "cnbc.com": "CNBC",
    "apnews.com": "the Associated Press",
    "bbc.co.uk": "the BBC",
    "bbc.com": "the BBC",
    "theguardian.com": "The Guardian",
    "arstechnica.com": "Ars Technica",
}


def outlet_for(url: str, extra: dict | None = None) -> str | None:
    """Publication name for an observation: known domain names first (the article's domain, or a Google
    News item's `outlet_url`), then the feed's outlet / Techmeme cite ("Name / Outlet"), then the domain."""
    ex = extra or {}
    for u in (ex.get("outlet_url"), url):
        if isinstance(u, str) and u:
            dom = registrable_domain(u)
            if dom in OUTLET_NAMES:
                return OUTLET_NAMES[dom]
    name = ex.get("outlet") or ex.get("cite")
    if isinstance(name, str) and name.strip():
        name = name.split(" / ")[-1].strip()  # Techmeme: "Author / Outlet"
        return re.sub(r"\.(com|org|net|co\.uk)$", "", name, flags=re.I) or None
    host = (urlsplit(url).hostname or "").lower()
    if host in {"news.google.com", "techmeme.com", "www.techmeme.com"}:
        return None
    return registrable_domain(url) or None


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").lower()
    return " ".join(re.sub(r"[^\w\s%$]", " ", t).split())


def informative_snippet(title: str, snippet: str | None, outlet: str | None = None) -> str:
    """The snippet if it says more than the headline, else "". Google News descriptions repeat the title and
    append the outlet; some feeds repeat the title verbatim."""
    s = clean_text(snippet or "")
    if not s:
        return ""
    rest = _norm(s)
    nt = _norm(title)
    if nt and rest.startswith(nt):
        rest = rest[len(nt) :].strip()
    if outlet:
        no = _norm(outlet)
        if no and rest.endswith(no):
            rest = rest[: -len(no)].strip()
        rest = re.sub(r"\b(com|org|net)$", "", rest).strip()
    if len(rest) < MIN_SNIPPET_CHARS:
        return ""
    if nt and fuzz.token_sort_ratio(_norm(s), nt) >= 90:
        return ""
    return s


def collect_source_snippets(
    items: Sequence[NormItem],
    url_order: Sequence[str],
    *,
    source_type: Callable[[str], str | None] = lambda _sid: None,
    limit: int = MAX_SNIPPETS,
) -> list[SourceSnippet]:
    """One snippet per distinct publisher text across a story's items, in the digest's URL order, deduped
    (near-identical summaries from two feeds count once), at most `limit`. Items without an informative
    snippet still give a headline-only entry when there is room (the headline is the publisher's text)."""
    pos = {u: i for i, u in enumerate(url_order)}
    ordered = sorted(items, key=lambda it: (pos.get(it.url, len(pos)), it.url))
    with_text: list[SourceSnippet] = []
    headline_only: list[SourceSnippet] = []
    seen: list[str] = []
    seen_titles: set[str] = set()
    for it in ordered:
        best: SourceSnippet | None = None
        for o in it.observations:
            if source_type(o.source_id) in NON_PUBLISHER_TYPES or o.source_id.startswith(("hn", "reddit")):
                continue
            outlet = outlet_for(o.url, o.extra)
            title = clean_text(o.title) or it.title
            text = informative_snippet(title, o.snippet, outlet)
            cand = SourceSnippet(
                url=it.url, outlet=outlet, title=title, snippet=text, published_at=o.published_at or None
            )
            if best is None or (text and len(text) > len(best.snippet)):
                best = cand
        if best is None:
            continue
        if best.snippet:
            key = _norm(best.snippet)
            if any(fuzz.ratio(key, k) >= 90 for k in seen):
                continue
            seen.append(key)
            with_text.append(best)
        elif _norm(best.title) not in seen_titles:
            headline_only.append(best)
        seen_titles.add(_norm(best.title))
    return (with_text + headline_only)[:limit]


__all__ = ["OUTLET_NAMES", "collect_source_snippets", "informative_snippet", "outlet_for"]
