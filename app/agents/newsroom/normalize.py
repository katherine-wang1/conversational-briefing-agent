"""N2 normalise: canonical URLs, aggregator-link resolution, dedupe, best title and snippet.

Pure code, no I/O. Aggregator links (Techmeme permalinks, HN item pages) are resolved offline from
the observations themselves: a redirect-style query parameter, the same HN item id seen with an
outbound URL, or an exact title match with a non-aggregator observation. Links that can't be
resolved are kept as they are and flagged (`NormItem.unresolved_aggregator`), and they don't count
as an independent domain.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from app.contracts.sources import HeadlineObservation

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "yclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "_hsenc",
    "_hsmi",
    "mkt_tok",
    "ref",
    "ref_src",
    "ref_url",
    "referrer",
    "cmpid",
    "smid",
    "smtyp",
    "ocid",
    "s_cid",
    "spm",
    "share",
    "sharetype",
    "src",
    "source",
    "via",
    "trk",
    "trkcampaign",
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
    "ncid",
    "sr_share",
    "taid",
    "tpcc",
    "itm_source",
    "itm_medium",
    "itm_campaign",
    "__twitter_impression",
    "rss",
    "feed",
    "ito",
    "at_medium",
    "at_campaign",
    "wt.mc_id",
    "_ga",
    "_gl",
    "hss_channel",
    "leadsource",
    "pk_campaign",
    "pk_kwd",
}
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "ga_", "vero_", "oly_", "__s")
REDIRECT_PARAMS = ("url", "u", "q", "target", "dest", "destination", "redirect", "to", "link")

# Multi-label public suffixes we see in news sources (a tiny stand-in for the public suffix list).
_TWO_LEVEL_SUFFIXES = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.au",
    "net.au",
    "org.au",
    "co.jp",
    "ne.jp",
    "or.jp",
    "co.kr",
    "co.in",
    "com.cn",
    "com.br",
    "co.nz",
    "com.sg",
    "com.hk",
    "com.tw",
    "co.za",
    "eu.org",
}

AGGREGATOR_HOSTS = {"techmeme.com", "news.ycombinator.com", "reddit.com", "old.reddit.com"}

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")
_WS = re.compile(r"\s+")
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
_HN_PREFIX = re.compile(r"^(show|ask|tell|launch) hn:\s*", re.I)


def clean_text(s: str | None) -> str:
    """Strip zero-width and control characters and collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = _ZERO_WIDTH.sub("", s)
    s = "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return _WS.sub(" ", s).strip()


def strip_invisible(s: str | None) -> str:
    """Remove zero-width and control characters but keep line breaks."""
    if not s:
        return ""
    s = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", s))
    return "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch)[0] != "C")


def _host(netloc: str) -> str:
    host = netloc.lower().rsplit("@", 1)[-1]
    if ":" in host:
        name, port = host.rsplit(":", 1)
        if port in ("80", "443", ""):
            host = name
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    return host.rstrip(".")


def registrable_domain(url_or_host: str) -> str:
    """Approximate eTLD+1, e.g. 'blog.openai.com' -> 'openai.com', 'bbc.co.uk' stays."""
    host = _host(urlsplit(url_or_host).netloc) if "://" in url_or_host else _host(url_or_host)
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in TRACKING_PARAMS or k.startswith(TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Canonical form used as the dedupe key.

    https, lower-case host without `www.`/`m.`, no default port, no fragment, no tracking params,
    remaining params sorted, no trailing slash. arXiv pdf/abs/versions and Hugging Face paper pages
    map to `https://arxiv.org/abs/<id>` so a paper seen in several listings is one item.
    """
    url = clean_text(url)
    if "://" not in url:
        url = "https://" + url.lstrip("/")
    parts = urlsplit(url)
    host = _host(parts.netloc)
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _is_tracking(k)]

    hf_paper = host == "huggingface.co" and path.startswith("/papers/")
    if host in ("arxiv.org", "export.arxiv.org") or hf_paper:
        m = _ARXIV_ID.search(path.split("/", 2)[-1] if host != "huggingface.co" else path[len("/papers/") :])
        if m:
            return f"https://arxiv.org/abs/{m.group(1)}"

    if host == "news.ycombinator.com":
        query = [(k, v) for k, v in query if k == "id"]
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    q = urlencode(sorted(query))
    return urlunsplit(("https", host, path, q, ""))


def is_aggregator_url(url: str) -> bool:
    host = _host(urlsplit(url).netloc)
    return host in AGGREGATOR_HOSTS or registrable_domain(host) in AGGREGATOR_HOSTS


def redirect_target(url: str) -> str | None:
    """Target of a redirect-style link (`...?url=https://...`), if any."""
    for k, v in parse_qsl(urlsplit(url).query):
        if k.lower() in REDIRECT_PARAMS:
            v = unquote(v)
            if v.startswith(("http://", "https://")) and not is_aggregator_url(v):
                return v
    return None


def hn_item_id(url: str) -> str | None:
    parts = urlsplit(url)
    if _host(parts.netloc) == "news.ycombinator.com" and parts.path.rstrip("/") == "/item":
        return dict(parse_qsl(parts.query)).get("id")
    return None


def title_key(title: str) -> str:
    t = _HN_PREFIX.sub("", clean_text(title)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


@dataclass
class NormItem:
    """One distinct article/post/paper after N2 (all observations of the same canonical URL)."""

    item_id: str  # = canonical URL
    url: str  # canonical URL
    domain: str
    title: str
    snippet: str
    first_seen: datetime
    published_at: datetime | None
    observations: list[HeadlineObservation] = field(default_factory=list)
    unresolved_aggregator: bool = False

    @property
    def source_ids(self) -> list[str]:
        return sorted({o.source_id for o in self.observations})

    @property
    def text(self) -> str:
        """Text that gets embedded (title + snippet, TDD §7 N3)."""
        return f"{self.title}. {self.snippet[:300]}" if self.snippet else self.title


def _title_rank(title: str, tier: int) -> tuple:
    t = clean_text(title)
    stripped = _HN_PREFIX.sub("", t)
    length_ok = 20 <= len(stripped) <= 200
    return (stripped != t, not length_ok, tier, -len(stripped), stripped)


def resolve_aggregator_links(observations: Sequence[HeadlineObservation]) -> dict[str, str]:
    """Map aggregator URLs (as given) to a target URL found offline. Missing key = unresolved."""
    by_hn_id: dict[str, str] = {}
    by_title: dict[str, set[str]] = defaultdict(set)
    for o in observations:
        if is_aggregator_url(o.url):
            continue
        if o.external_id and o.source_id.startswith("hn"):
            by_hn_id.setdefault(o.external_id, o.url)
        by_title[title_key(o.title)].add(o.url)

    out: dict[str, str] = {}
    for o in observations:
        if not is_aggregator_url(o.url):
            continue
        target = redirect_target(o.url)
        if target is None:
            hid = hn_item_id(o.url) or (o.external_id if o.source_id.startswith("hn") else None)
            target = by_hn_id.get(hid) if hid else None
        if target is None:
            cands = by_title.get(title_key(o.title), set())
            if len({canonical_url(c) for c in cands}) == 1:
                target = sorted(cands)[0]
        if target is not None:
            out[o.url] = target
    return out


def normalize(
    observations: Iterable[HeadlineObservation],
    source_tiers: dict[str, int] | None = None,
) -> list[NormItem]:
    """Dedupe observations into items keyed by canonical URL; sorted by item_id (deterministic)."""
    obs = sorted(observations, key=lambda o: (o.observed_at, o.source_id, o.rank or 10**6, o.url, o.title))
    tiers = source_tiers or {}
    links = resolve_aggregator_links(obs)
    groups: dict[str, list[HeadlineObservation]] = defaultdict(list)
    unresolved: set[str] = set()
    seen: set[tuple] = set()
    for o in obs:
        key = (o.source_id, o.observed_at, o.url)
        if key in seen:  # exact duplicate observation
            continue
        seen.add(key)
        target = links.get(o.url, o.url)
        cu = canonical_url(target)
        if is_aggregator_url(target):
            unresolved.add(cu)
        groups[cu].append(o)

    items: list[NormItem] = []
    for cu in sorted(groups):
        group = groups[cu]
        best_title = min(
            (o for o in group if clean_text(o.title)),
            key=lambda o: _title_rank(o.title, tiers.get(o.source_id, 9)),
            default=group[0],
        )
        snippets = sorted(
            {clean_text(o.snippet) for o in group if clean_text(o.snippet)}, key=lambda s: (-len(s), s)
        )
        published = [o.published_at for o in group if o.published_at is not None]
        items.append(
            NormItem(
                item_id=cu,
                url=cu,
                domain=registrable_domain(cu),
                title=_HN_PREFIX.sub("", clean_text(best_title.title)),
                snippet=snippets[0][:600] if snippets else "",
                first_seen=min(o.observed_at for o in group),
                published_at=min(published) if published else None,
                observations=group,
                unresolved_aggregator=cu in unresolved,
            )
        )
    return items
