"""Free coverage of the same event, for a story whose listed sources are all paywalled, blocked or failed.

One search through the `search` tool (its live or replay backend; the code is the same) for the story's
headline in the story's time window. A hit is accepted only if it is
- on an open domain: not a known paywall (`newsroom_editor.paywalled_domains`), not an aggregator or social
  site, not a URL the story already tried;
- a close match: rapidfuzz title similarity to the story's headline (or a publisher's own title for it)
  plus enough of the story's entities named in the hit's title and snippet;
- published inside the window (undated hits are rejected unless `allow_undated`).

Accepted hits are ranked primary-looking sources first (a company's own site or newsroom, a regulator or
government site, a press-release wire, a configured primary source), then by match score. Callers fetch
them through the `fetch` tool and check the fetched text names the entities too (`content_matches`) before
using it as a normal source. Paywalls are never bypassed: a paywalled hit is rejected, not rewritten.

Used by story notes (app/agents/newsroom/notes.py; shared, so once per story per run) and as a C6 fallback
(app/agents/cos/packs.py). Without a search provider the search fails and the step is skipped.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

from app.agents.cos.text import canonical_url
from app.agents.cos.tooling import ToolUnavailable, search
from app.contracts.common import BudgetExceeded
from app.contracts.sources import SearchHit
from app.contracts.tools import Tool, ToolContext

FOUND_VIA = "free_coverage_search"

# Never sources for a story: aggregators, discussion and social sites (low quality, or not the event).
NOT_SOURCES = (
    "news.google.com",
    "techmeme.com",
    "news.ycombinator.com",
    "reddit.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "tiktok.com",
    "threads.net",
    "msn.com",  # mostly syndicated wire copy behind a script wall; the extractor gets little text
    "yahoo.com/news/videos",
)
PRESS_WIRES = ("prnewswire.com", "businesswire.com", "globenewswire.com", "accessnewswire.com")
GOV_SUFFIXES = (".gov", ".gov.uk", ".gouv.fr", ".europa.eu", ".gc.ca", ".gov.au", ".go.jp", ".mil")
GENERIC_ENTITIES = frozenset(
    {
        "us",
        "u s",
        "usa",
        "united states",
        "uk",
        "eu",
        "china",
        "ai",
        "the us",
        "government",
        "congress",
        "senate",
        "white house",
        "europe",
        "japan",
        "india",
        "world",
    }
)
_STOP = frozenset(
    "a an the and or of to in on for with by at from as is are be after before over amid into says said "
    "report reports reportedly new its it this that than more about up out".split()
)


class FreeCoverageSettings(BaseModel):
    """`story_notes.free_coverage:` in config/agent.yaml (C6 reads the same block)."""

    model_config = ConfigDict(extra="forbid")
    max_searches: int = 25  # per story-notes run (one digest); C6 is bounded by the user's search budget
    max_results: int = 8
    max_fetches: int = 2  # accepted hits fetched per story until one gives text
    min_title_similarity: float = 50.0  # rapidfuzz 0-100 (mean of token_set and token_sort ratios)
    strong_title_similarity: float = 80.0  # at or above: one entity is enough
    min_entities: int = 2  # entities named in the hit (capped by how many distinctive ones the story has)
    window_hours: float = 36.0  # hits published from (story first seen - this) to now
    allow_undated: bool = False
    blocked_domains: list[str] = list(NOT_SOURCES)


# ---- text helpers ---------------------------------------------------------------------------------------


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"'s\b", "", t)
    t = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b|\s|$)", "", t)  # "u.s." -> "us"
    return " ".join(re.sub(r"[^\w\s%$]", " ", t).split())


def url_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _domain_in(url: str, domains: Iterable[str]) -> bool:
    host = url_host(url)
    path = (urlsplit(url).path or "").lower()
    for d in domains:
        d = d.lower().strip(".")
        if not d:
            continue
        dhost, _, dpath = d.partition("/")
        if (host == dhost or host.endswith("." + dhost)) and (not dpath or path.startswith("/" + dpath)):
            return True
    return False


def strip_outlet_suffix(title: str) -> str:
    """'Title - Outlet' / 'Title | Outlet' -> 'Title' (when what's left is long enough)."""
    m = re.match(r"^(.{20,}?)\s+[-|–—]\s+[^-|–—]{2,60}$", (title or "").strip())
    return m.group(1) if m else (title or "").strip()


def coverage_query(headline: str, max_words: int = 14) -> str:
    """The headline's first clause (editor headlines join two events with ';'), without parentheses or the
    outlet suffix, at most `max_words` words."""
    h = strip_outlet_suffix(headline).split(";")[0]
    h = re.sub(r"\([^)]*\)", " ", h)
    words = h.replace("“", '"').replace("”", '"').split()
    return " ".join(words[:max_words]).strip(" ,.:")


def title_similarity(a: str, b: str) -> float:
    """0-100: mean of rapidfuzz token_set_ratio and token_sort_ratio on normalised titles (token_set alone
    scores a short title that is a subset of the other at 100)."""
    x, y = _norm(strip_outlet_suffix(a)), _norm(strip_outlet_suffix(b))
    if not x or not y:
        return 0.0
    return (fuzz.token_set_ratio(x, y) + fuzz.token_sort_ratio(x, y)) / 2


def key_entities(entities: Sequence[str], headline: str) -> list[str]:
    """Distinctive entities for the overlap check: the newsroom's entities minus generic ones (countries,
    "AI"); if none are left, capitalised words and numbers from the headline."""
    out: list[str] = []
    for e in entities:
        n = _norm(e)
        if n and n not in GENERIC_ENTITIES and n not in {_norm(o) for o in out}:
            out.append(e)
    if out:
        return out
    toks = re.findall(r"\b(?:[A-Z][\w&.-]+|\d[\d,.]*%?)", strip_outlet_suffix(headline))
    for t in toks:
        n = _norm(t)
        if not n or n in _STOP or n in GENERIC_ENTITIES:
            continue
        if n not in {_norm(o) for o in out}:
            out.append(t)
    return out[:6]


def entity_in(entity: str, text_norm: str) -> bool:
    """Whole-word match of the entity, or of its last word for a multi-word name ("Jensen Huang" ->
    "huang") when that word is distinctive (5+ letters)."""
    n = _norm(entity)
    if not n:
        return False
    if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text_norm):
        return True
    parts = [p for p in n.split() if p not in _STOP]
    if len(parts) > 1 and len(parts[-1]) >= 5:
        return re.search(rf"(?<!\w){re.escape(parts[-1])}(?!\w)", text_norm) is not None
    return False


def entities_found(entities: Sequence[str], text: str) -> list[str]:
    tn = _norm(text)
    return [e for e in entities if entity_in(e, tn)]


def required_entities(entities: Sequence[str], st: FreeCoverageSettings) -> int:
    return min(st.min_entities, len(entities)) if entities else 0


def content_matches(text: str, entities: Sequence[str], st: FreeCoverageSettings) -> bool:
    """A fetched page must name enough of the story's entities too (catches a hit whose title matched but
    whose page is about something else, or an error page)."""
    need = max(1, required_entities(entities, st)) if entities else 0
    return len(entities_found(entities, text[:20000])) >= need


def primary_hint(url: str, entities: Sequence[str], primary: Iterable[str] = ()) -> bool:
    """Looks like the primary source: a configured primary domain, a government / regulator site, a press
    release wire, or the site of a company the story names (sap.com for "SAP", news.sap.com too)."""
    host = url_host(url)
    if _domain_in(url, primary) or _domain_in(url, PRESS_WIRES):
        return True
    if host.endswith(GOV_SUFFIXES) or host == "sec.gov" or host.endswith(".sec.gov"):
        return True
    labels = host.split(".")
    site = labels[-2] if len(labels) >= 2 else host
    for e in entities:
        key = re.sub(r"[^a-z0-9]", "", _norm(e))
        if len(key) >= 3 and (site == key or (len(key) >= 5 and site.startswith(key))):
            return True
    return False


# ---- acceptance -----------------------------------------------------------------------------------------


@dataclass
class Verdict:
    url: str
    title: str
    accepted: bool
    reason: str  # why rejected, or the match summary when accepted
    title_sim: float = 0.0
    entities: list[str] = field(default_factory=list)
    primary: bool = False
    published_at: datetime | None = None
    hit: SearchHit | None = None

    @property
    def score(self) -> float:
        return self.title_sim + 10 * len(self.entities)

    def match_text(self, n_entities: int) -> str:
        return (
            f"title {self.title_sim:.0f}, entities {len(self.entities)}/{n_entities}"
            + (", primary" if self.primary else "")
            + (f", published {self.published_at:%Y-%m-%d %H:%M}" if self.published_at else "")
        )


def _aware(d: datetime, like: datetime) -> datetime:
    if (d.tzinfo is None) != (like.tzinfo is None):
        return d.replace(tzinfo=like.tzinfo)
    return d


def judge_hit(
    hit: SearchHit,
    *,
    ref_titles: Sequence[str],
    entities: Sequence[str],
    since: datetime,
    until: datetime,
    paywall: Iterable[str],
    exclude_urls: Iterable[str] = (),
    primary: Iterable[str] = (),
    st: FreeCoverageSettings,
) -> Verdict:
    v = Verdict(
        url=hit.url, title=hit.title, accepted=False, reason="", published_at=hit.published_at, hit=hit
    )
    if not hit.url.startswith(("http://", "https://")):
        v.reason = "not a web URL"
        return v
    if canonical_url(hit.url) in {canonical_url(u) for u in exclude_urls}:
        v.reason = "already tried"
        return v
    if _domain_in(hit.url, paywall):
        v.reason = f"paywalled domain {url_host(hit.url)}"
        return v
    if _domain_in(hit.url, st.blocked_domains):
        v.reason = f"not a source ({url_host(hit.url)})"
        return v
    if hit.published_at is None:
        if not st.allow_undated:
            v.reason = "no publication date"
            return v
    else:
        p = hit.published_at
        if _aware(p, since) < since or _aware(p, until) > until + timedelta(hours=1):
            v.reason = f"outside window ({p:%Y-%m-%d %H:%M})"
            return v
    v.title_sim = max((title_similarity(t, hit.title) for t in ref_titles if t), default=0.0)
    v.entities = entities_found(entities, f"{hit.title}\n{hit.snippet}")
    need = required_entities(entities, st)
    v.primary = primary_hint(hit.url, entities, primary)
    if v.title_sim >= st.strong_title_similarity and (len(v.entities) >= min(1, need)):
        v.accepted = True
    elif v.title_sim >= st.min_title_similarity and len(v.entities) >= need:
        v.accepted = True
    if v.accepted:
        v.reason = v.match_text(len(entities))
    elif v.title_sim < st.min_title_similarity:
        v.reason = f"low title similarity {v.title_sim:.0f}"
    else:
        v.reason = f"entities {len(v.entities)}/{len(entities)} < {need}"
    return v


@dataclass
class CoverageSearch:
    """One free-coverage search for one story."""

    query: str
    since: datetime
    until: datetime
    searched: bool = False
    error: str | None = None
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def accepted(self) -> list[Verdict]:
        """Accepted hits, primary-looking first, then by score."""
        acc = [v for v in self.verdicts if v.accepted]
        return sorted(acc, key=lambda v: (not v.primary, -v.score, v.url))

    def summary(self) -> dict:
        return {
            "query": self.query,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "searched": self.searched,
            "error": self.error,
            "hits": len(self.verdicts),
            "accepted": [{"url": v.url, "match": v.reason} for v in self.accepted],
            "rejected": [{"url": v.url, "reason": v.reason} for v in self.verdicts if not v.accepted][:8],
        }


def search_window(
    anchor: datetime | None, now: datetime, st: FreeCoverageSettings
) -> tuple[datetime, datetime]:
    start = min(anchor or now, now) - timedelta(hours=st.window_hours)
    return start, now


async def search_free_coverage(
    tools: dict[str, Tool],
    ctx: ToolContext,
    *,
    headline: str,
    ref_titles: Sequence[str] = (),
    entities: Sequence[str] = (),
    anchor: datetime | None,
    now: datetime,
    paywall: Iterable[str],
    exclude_urls: Iterable[str] = (),
    primary: Iterable[str] = (),
    st: FreeCoverageSettings,
) -> CoverageSearch:
    """One search and the verdict on each hit. Never raises for a search failure (see `error`);
    `BudgetExceeded` from the run's search budget is reported the same way."""
    since, until = search_window(anchor, now, st)
    cs = CoverageSearch(query=coverage_query(headline), since=since, until=until)
    keys = key_entities(entities, headline)
    titles = [headline, *ref_titles]
    try:
        hits = await search(tools, ctx, cs.query, since=since, until=until, max_results=st.max_results)
    except (ToolUnavailable, BudgetExceeded) as e:
        cs.error = f"{type(e).__name__}: {e}"[:200]
        return cs
    cs.searched = True
    paywall, exclude, primary = list(paywall), list(exclude_urls), list(primary)
    for h in hits:
        cs.verdicts.append(
            judge_hit(
                h,
                ref_titles=titles,
                entities=keys,
                since=since,
                until=until,
                paywall=paywall,
                exclude_urls=exclude,
                primary=primary,
                st=st,
            )
        )
    return cs


def search_unavailable(error: str | None) -> bool:
    """The search tool has no provider (or none at all): skip free coverage for the rest of the run."""
    e = (error or "").lower()
    return "no search tool" in e or "not configured" in e or "api key" in e


__all__ = [
    "FOUND_VIA",
    "CoverageSearch",
    "FreeCoverageSettings",
    "Verdict",
    "content_matches",
    "coverage_query",
    "entities_found",
    "judge_hit",
    "key_entities",
    "primary_hint",
    "search_free_coverage",
    "search_unavailable",
    "title_similarity",
]
