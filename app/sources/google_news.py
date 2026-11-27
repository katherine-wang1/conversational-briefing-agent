"""Resolve Google News redirect links (`news.google.com/rss/articles/CBMi…`) to the publisher's URL.

Google News RSS items link to `news.google.com`, not to the outlet. Those pages are a JavaScript
redirect the fetch tool can't follow, so a story whose only links are Google News links gets no
article text. Three ways to get the publisher URL, tried in this order:

1. **offline**: the article id is URL-safe base64 of a small protobuf. Older ids (before mid-2024)
   carry the publisher URL inside it (`08 13 22 <varint len> <url> …`); they decode without a request.
2. **batchexecute**: newer ids carry an opaque token (decoded bytes start with `AU_yqL`). The article
   page (`/rss/articles/<id>`) has a signature and timestamp (`data-n-a-sg`, `data-n-a-ts`); a POST to
   Google News' `DotsSplashUi/data/batchexecute` RPC `Fbv4je` ("garturlreq") with id + timestamp +
   signature answers `["garturlres", "<publisher url>", 1]`. Several ids go in one POST.
   The approach of the open-source `googlenewsdecoder` package; verified live on 2026-11-27.
3. **search** (fallback, only with a search provider and a title hint): Google News titles end in
   " - Publisher" and the feed gives the outlet's home page (`<source href>`). Search the title,
   keep hits on the outlet's domain, and accept one only if its title closely matches.

Otherwise the link stays unresolved (`method="failed"`); nothing is guessed.

Politeness: the project's honest User-Agent, at most `concurrency` page requests at a time, timeouts,
retries with exponential backoff on 429 / 5xx / network errors. Results are cached in memory and,
when a path is given, in a small JSON file (`ResolutionCache`); failures are only cached in memory,
for `failure_ttl_s`, so a later run tries again.

Risk: (2) is an undocumented internal RPC. If Google changes it, resolution falls back to (3) or
fails; it never returns a wrong URL silently (the RPC answer must be an http(s) URL off Google).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel

from app.sources.util import USER_AGENT

log = logging.getLogger(__name__)

GOOGLE_NEWS_HOSTS = frozenset({"news.google.com"})
ARTICLE_PAGE = "https://news.google.com/rss/articles/{id}"
ARTICLE_PAGE_ALT = "https://news.google.com/articles/{id}"
BATCHEXECUTE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
PAGE_PARAMS = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
MAX_PAGE_BYTES = 3_000_000
TITLE_MATCH_MIN = 0.85  # title similarity needed to accept a search hit on the outlet's domain
TITLE_MATCH_MIN_NO_DOMAIN = 0.93  # ... when the outlet's domain is unknown

Method = Literal["offline", "batchexecute", "search", "failed", "not_google_news"]

_ID_RE = re.compile(r"/(?:rss/)?articles/([A-Za-z0-9_-]+)")
_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')


class Resolution(BaseModel):
    """How one URL was resolved. `resolved_url` is None unless `method` is offline/batchexecute/search."""

    url: str
    resolved_url: str | None = None
    method: Method
    cached: bool = False
    detail: str | None = None  # failure reason, or the search match ("title similarity 0.97")
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.resolved_url is not None


@dataclass
class Hint:
    """What the feed told us about the article: its title (with or without " - Outlet"), the outlet's
    name and home page (`<source href>`). Only used by the search fallback."""

    title: str | None = None
    outlet: str | None = None
    outlet_url: str | None = None


# ---- url helpers ----------------------------------------------------------------------------------


def _hostname(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_google_news_url(url: str | None) -> bool:
    """A Google News article redirect (`/rss/articles/<id>`, `/articles/<id>`, `/read/<id>`)."""
    if not url:
        return False
    if _hostname(url) not in GOOGLE_NEWS_HOSTS:
        return False
    return article_id(url) is not None


def article_id(url: str) -> str | None:
    path = urlsplit(url).path
    m = _ID_RE.search(path) or re.search(r"/read/([A-Za-z0-9_-]+)", path)
    return m.group(1) if m else None


def _is_publisher_url(url: Any) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    host = _hostname(url)
    return bool(host) and "." in host and not (host == "google.com" or host.endswith(".google.com"))


def registrable_domain(url_or_host: str) -> str:
    """Approximate eTLD+1 ('www.reuters.com' -> 'reuters.com', 'bbc.co.uk' stays)."""
    host = _hostname(url_or_host) if "://" in url_or_host else url_or_host.lower()
    parts = host.removeprefix("www.").split(".")
    if len(parts) >= 3 and (
        parts[-2] in {"co", "com", "org", "net", "ac", "gov", "edu"} and len(parts[-1]) == 2
    ):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ---- offline decoding -----------------------------------------------------------------------------


def _b64(s: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (ValueError, TypeError):
        return None


def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = n = 0
    while i < len(b):
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7
    raise ValueError("truncated varint")


def decode_offline(gid: str) -> str | None:
    """Publisher URL from an old-style article id, or None (new-style id, or not decodable)."""
    raw = _b64(gid)
    if not raw or not raw.startswith(b"\x08\x13\x22"):
        return None
    try:
        n, i = _varint(raw, 3)
    except ValueError:
        return None
    payload = raw[i : i + n]
    if payload.startswith(b"AU_yqL"):
        return None  # new-style token: needs batchexecute
    try:
        url = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return url if _is_publisher_url(url) else None


def needs_online(gid: str) -> bool:
    raw = _b64(gid) or b""
    return b"AU_yqL" in raw[:12]


# ---- batchexecute ---------------------------------------------------------------------------------


def parse_signature(page_html: str) -> tuple[str, str] | None:
    """(signature, timestamp) from a Google News article page, or None."""
    sg, ts = _SG_RE.search(page_html), _TS_RE.search(page_html)
    if not (sg and ts) or not ts.group(1).isdigit():
        return None
    return sg.group(1), ts.group(1)


def batchexecute_payload(items: Sequence[tuple[str, str, str]]) -> str:
    """Form body for (id, timestamp, signature) triples; request i is tagged str(i+1)."""
    reqs = []
    for n, (gid, ts, sg) in enumerate(items):
        inner = (
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
            f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],{json.dumps(gid)},{int(ts)},{json.dumps(sg)}]'
        )
        reqs.append(["Fbv4je", inner, None, str(n + 1)])
    return "f.req=" + quote(json.dumps([reqs], separators=(",", ":")))


def parse_batchexecute(text: str) -> dict[int, str]:
    """{request index: publisher url} from a batchexecute response (`)]}'` + JSON array)."""
    out: dict[int, str] = {}
    body = text.split("\n\n", 1)[1] if "\n\n" in text else text.removeprefix(")]}'")
    try:
        envelopes = json.loads(body)
    except json.JSONDecodeError:
        return out
    for env in envelopes if isinstance(envelopes, list) else []:
        if not (isinstance(env, list) and len(env) >= 3 and env[0] == "wrb.fr" and env[1] == "Fbv4je"):
            continue
        try:
            inner = json.loads(env[2]) if isinstance(env[2], str) else None
        except json.JSONDecodeError:
            continue
        tag = env[6] if len(env) > 6 else None
        idx = int(tag) - 1 if isinstance(tag, str) and tag.isdigit() else len(out)
        if (
            isinstance(inner, list)
            and len(inner) >= 2
            and inner[0] == "garturlres"
            and _is_publisher_url(inner[1])
        ):
            out[idx] = inner[1]
    return out


# ---- search fallback ------------------------------------------------------------------------------


def _norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower()
    t = re.sub(r"[‘’“”\"'`]", "", t)
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def _title_variants(t: str) -> list[str]:
    """The title, and the title without a trailing " - Outlet" / " | Outlet" (if what's left is long)."""
    out = [t]
    m = re.match(r"^(.{20,}?)\s+[-|–—]\s+[^-|–—]{2,60}$", t.strip())
    if m:
        out.append(m.group(1))
    return out


def title_similarity(a: str, b: str) -> float:
    best = 0.0
    for x in _title_variants(a):
        for y in _title_variants(b):
            nx, ny = _norm_title(x), _norm_title(y)
            if nx and ny:
                best = max(best, SequenceMatcher(None, nx, ny).ratio())
    return best


def split_outlet(title: str, outlet: str | None = None) -> tuple[str, str | None]:
    """('Title', 'Outlet') from a Google News title 'Title - Outlet' (the outlet hint wins if given)."""
    t = title.strip()
    if outlet and t.endswith(f" - {outlet}") and len(t) > len(outlet) + 3:
        return t[: -len(outlet) - 3].rstrip(), outlet
    if outlet:
        return t, outlet
    m = re.match(r"^(.{10,}) - ([^-]{2,60})$", t)
    return (m.group(1).rstrip(), m.group(2).strip()) if m else (t, None)


SearchFn = Callable[[str, int], Awaitable[list[Any]]]  # (query, max_results) -> hits with .url/.title


# ---- cache ----------------------------------------------------------------------------------------


class ResolutionCache:
    """Successful resolutions by article id: in memory, plus a small JSON file if `path` is set.
    Failures stay in memory for `failure_ttl_s` only. Bounded to `max_entries` (oldest dropped)."""

    def __init__(
        self, path: str | Path | None = None, *, max_entries: int = 5000, failure_ttl_s: float = 3600
    ):
        self.path = Path(path) if path else None
        self.max_entries = max_entries
        self.failure_ttl_s = failure_ttl_s
        self._ok: dict[str, dict] = {}
        self._failed: dict[str, tuple[float, str]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if isinstance(data, dict):
                    self._ok = {
                        k: v for k, v in data.items() if isinstance(v, dict) and v.get("resolved_url")
                    }
            except (OSError, json.JSONDecodeError) as e:
                log.warning("google news cache unreadable (%s); starting empty", type(e).__name__)

    def get(self, gid: str) -> dict | None:
        self._load()
        return self._ok.get(gid)

    def failed_recently(self, gid: str) -> str | None:
        f = self._failed.get(gid)
        if f and time.monotonic() - f[0] < self.failure_ttl_s:
            return f[1]
        return None

    def put(self, gid: str, resolved_url: str, method: str) -> None:
        self._load()
        self._ok.pop(gid, None)
        self._ok[gid] = {"resolved_url": resolved_url, "method": method, "at": datetime.now(UTC).isoformat()}
        self._failed.pop(gid, None)
        while len(self._ok) > self.max_entries:
            self._ok.pop(next(iter(self._ok)))
        self._save()

    def put_failure(self, gid: str, reason: str) -> None:
        self._failed[gid] = (time.monotonic(), reason)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self._ok))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("google news cache not saved (%s)", type(e).__name__)


# ---- resolver -------------------------------------------------------------------------------------


class GoogleNewsResolver:
    """Resolve Google News links to publisher URLs (see the module docstring).

    `search`: an async `(query, max_results) -> hits` (hits have `.url` and `.title`), e.g. a search
    provider's `search` wrapped by `search_fn_from_provider`. None disables the search fallback.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        search: SearchFn | None = None,
        cache: ResolutionCache | None = None,
        concurrency: int = 2,
        timeout_s: float = 10.0,
        retries: int = 2,
        backoff_s: float = 1.0,
        batch_size: int = 10,
    ):
        self.client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
        self.search = search
        self.cache = cache or ResolutionCache()
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.batch_size = max(1, batch_size)
        self.hints: dict[str, Hint] = {}

    # -- hints

    def hint(
        self, url: str, *, title: str | None = None, outlet: str | None = None, outlet_url: str | None = None
    ):
        """Remember what a feed said about `url` (for the search fallback in later `resolve` calls)."""
        gid = article_id(url) if is_google_news_url(url) else None
        if gid and (title or outlet_url):
            self.hints[gid] = Hint(title=title, outlet=outlet, outlet_url=outlet_url)

    # -- http

    async def _request(self, method: str, url: str, **kw: Any) -> httpx.Response | None:
        headers = {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
        last = None
        for attempt in range(self.retries + 1):
            try:
                async with self.sem:
                    r = await self.client.request(method, url, headers=headers, timeout=self.timeout_s, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                elif "google.com/sorry" in str(r.url):
                    last = "rate limited (sorry page)"
                else:
                    return r
            except httpx.HTTPError as e:
                last = type(e).__name__
            if attempt < self.retries:
                await asyncio.sleep(self.backoff_s * 2**attempt)
        log.info("google news %s %s failed: %s", method, url[:80], last)
        return None

    async def _signature(self, gid: str) -> tuple[str, str] | str:
        """(signature, timestamp), or an error string."""
        err = "no signature on article page"
        for tmpl in (ARTICLE_PAGE, ARTICLE_PAGE_ALT):
            r = await self._request("GET", tmpl.format(id=gid), params=PAGE_PARAMS)
            if r is None:
                err = "article page unavailable"
                continue
            if r.status_code >= 400:
                err = f"article page HTTP {r.status_code}"
                continue
            if len(r.content) > MAX_PAGE_BYTES:
                err = "article page too large"
                continue
            sig = parse_signature(r.text)
            if sig:
                return sig
        return err

    async def _batchexecute(self, triples: list[tuple[str, str, str]]) -> dict[int, str] | str:
        r = await self._request(
            "POST",
            BATCHEXECUTE,
            content=batchexecute_payload(triples),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        if r is None:
            return "batchexecute unavailable"
        if r.status_code >= 400:
            return f"batchexecute HTTP {r.status_code}"
        return parse_batchexecute(r.text)

    async def _search(self, gid: str, h: Hint) -> tuple[str, str] | str:
        """(url, detail) of a confident search match, or an error string."""
        if self.search is None:
            return "no search provider"
        if not h.title:
            return "no title to search"
        title, outlet = split_outlet(h.title, h.outlet)
        domain = (
            registrable_domain(h.outlet_url) if h.outlet_url and _is_publisher_url(h.outlet_url) else None
        )
        # With a known domain: "<title> site:<domain>" first, then the bare title (providers honour
        # `site:` unevenly; live check 2026-11-27), keeping only hits on that domain either way.
        if domain:
            queries = [f"{title} site:{domain}", title]
        else:
            queries = [f"{title} {outlet}" if outlet else title]
        need = TITLE_MATCH_MIN if domain else TITLE_MATCH_MIN_NO_DOMAIN
        best: tuple[float, str] | None = None
        for query in queries:
            try:
                hits = await self.search(query, 8)
            except Exception as e:  # noqa: BLE001 - a failed fallback just leaves the link unresolved
                return f"search failed ({type(e).__name__})"
            for hit in hits:
                url, htitle = getattr(hit, "url", None), getattr(hit, "title", None) or ""
                if not _is_publisher_url(url):
                    continue
                if domain and registrable_domain(url) != domain:
                    continue
                sim = title_similarity(title, htitle)
                if best is None or sim > best[0]:
                    best = (sim, url)
            if best is not None and best[0] >= need:
                break
        if best is None:
            return "search: no hit" + (f" on {domain}" if domain else "")
        if best[0] < need:
            return f"search: best title similarity {best[0]:.2f} < {need}"
        return best[1], f"title similarity {best[0]:.2f}" + (f" on {domain}" if domain else "")

    # -- public

    async def resolve(self, url: str, hint: Hint | None = None) -> Resolution:
        return (await self.resolve_many([url], hints={url: hint} if hint else None))[0]

    async def resolve_many(
        self, urls: Sequence[str], hints: dict[str, Hint] | None = None
    ) -> list[Resolution]:
        """Resolve each URL (non-Google-News URLs come back as `not_google_news`). Same order as `urls`."""
        t0 = time.monotonic()
        res: dict[str, Resolution] = {}
        starts: dict[str, float] = {}
        todo: dict[str, str] = {}  # gid -> url (first url seen for that id)
        for url in dict.fromkeys(urls):
            starts[url] = time.monotonic()
            gid = article_id(url) if is_google_news_url(url) else None
            if gid is None:
                res[url] = Resolution(url=url, resolved_url=url, method="not_google_news")
                continue
            hit = self.cache.get(gid)
            if hit:
                res[url] = Resolution(
                    url=url, resolved_url=hit["resolved_url"], method=hit["method"], cached=True
                )
                continue
            why = self.cache.failed_recently(gid)
            if why:
                res[url] = Resolution(url=url, method="failed", cached=True, detail=why)
                continue
            dec = decode_offline(gid)
            if dec:
                self.cache.put(gid, dec, "offline")
                res[url] = Resolution(url=url, resolved_url=dec, method="offline")
                continue
            todo.setdefault(gid, url)

        errors: dict[str, str] = {}
        resolved: dict[str, tuple[str, Method, str | None]] = {}
        if todo:
            gids = list(todo)
            sigs = await asyncio.gather(*(self._signature(g) for g in gids))
            triples = []
            for g, s in zip(gids, sigs, strict=True):
                if isinstance(s, tuple):
                    triples.append((g, s[1], s[0]))
                else:
                    errors[g] = s
            for i in range(0, len(triples), self.batch_size):
                chunk = triples[i : i + self.batch_size]
                out = await self._batchexecute(chunk)
                for j, (g, _, _) in enumerate(chunk):
                    if isinstance(out, dict) and j in out:
                        resolved[g] = (out[j], "batchexecute", None)
                    else:
                        errors[g] = out if isinstance(out, str) else "batchexecute: no URL in answer"
            for g in [g for g in gids if g not in resolved]:
                h = (hints or {}).get(todo[g]) or self.hints.get(g)
                if h is None or self.search is None:
                    continue
                s = await self._search(g, h)
                if isinstance(s, tuple):
                    resolved[g] = (s[0], "search", s[1])
                else:
                    errors[g] = f"{errors.get(g, '')}; {s}".lstrip("; ")
            for g, url in todo.items():
                el = time.monotonic() - starts[url]
                if g in resolved:
                    target, method, detail = resolved[g]
                    self.cache.put(g, target, method)
                    res[url] = Resolution(
                        url=url, resolved_url=target, method=method, detail=detail, elapsed_s=el
                    )
                else:
                    reason = errors.get(g, "unresolved")
                    self.cache.put_failure(g, reason)
                    res[url] = Resolution(url=url, method="failed", detail=reason, elapsed_s=el)

        out = []
        for url in urls:
            r = res[url]
            if not r.elapsed_s:
                r = r.model_copy(update={"elapsed_s": round(time.monotonic() - starts.get(url, t0), 4)})
            else:
                r = r.model_copy(update={"elapsed_s": round(r.elapsed_s, 4)})
            out.append(r)
        return out


def search_fn_from_provider(provider: Any) -> SearchFn | None:
    """Adapt an `app.tools.search` provider (or None) to the resolver's `search` callable."""
    if provider is None:
        return None
    from app.contracts.tools import SearchArgs

    async def fn(query: str, max_results: int) -> list[Any]:
        return await provider.search(SearchArgs(query=query[:400], max_results=min(8, max_results)))

    return fn
