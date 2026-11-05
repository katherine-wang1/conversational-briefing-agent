"""`search` tool (TDD §5.2).

Live: one adapter chosen by SEARCH_PROVIDER (tavily | exa | brave) with SEARCH_API_KEY. Without a key
the live backend returns `search_unavailable` — never fabricated results.
Replay: SQLite FTS5 (BM25) over the archive, filtered to what was available at `ctx.now`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import httpx

from app.contracts.sources import SearchHit
from app.contracts.tools import SearchArgs, SearchResult, ToolContext
from app.tools.archive import ArchiveReader
from app.tools.base import Backend, CoreTool, ToolError
from app.tools.untrusted import sanitize_text, strip_tags
from app.tools.urls import hostname, url_hash

SNIPPET_CHARS = 300


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    for fmt in (None, "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d"):
        try:
            if fmt is None:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _hit(
    url: str, title: str | None, snippet: str | None, published: str | None, source: str | None
) -> SearchHit:
    return SearchHit(
        doc_id=url_hash(url),
        url=url,
        title=sanitize_text(title or url)[:300],
        source=source or hostname(url),
        published_at=_parse_dt(published),
        snippet=sanitize_text(snippet or "")[:SNIPPET_CHARS],
    )


class SearchProvider(Protocol):
    name: str

    async def search(self, args: SearchArgs) -> list[SearchHit]: ...


class _HttpProvider:
    name = "base"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None, timeout_s: float = 8.0):
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=timeout_s)

    def _check(self, r: httpx.Response) -> dict:
        if r.status_code in (401, 403):
            raise ToolError("search_unavailable", f"{self.name} rejected the API key (HTTP {r.status_code})")
        if r.status_code == 429:
            raise ToolError("search_unavailable", f"{self.name} rate limit (HTTP 429)")
        if r.status_code >= 400:
            raise ToolError("search_unavailable", f"{self.name} returned HTTP {r.status_code}")
        return r.json()


class TavilyProvider(_HttpProvider):
    name = "tavily"
    url = "https://api.tavily.com/search"

    async def search(self, args: SearchArgs) -> list[SearchHit]:
        body: dict = {
            "query": args.query,
            "max_results": args.max_results,
            "search_depth": "basic",
            "topic": "general",
            "include_answer": False,
        }
        if args.since:
            body["start_date"] = args.since.date().isoformat()
        if args.until:
            body["end_date"] = args.until.date().isoformat()
        r = await self.client.post(self.url, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        data = self._check(r)
        return [
            _hit(x["url"], x.get("title"), x.get("content"), x.get("published_date"), None)
            for x in data.get("results", [])
            if x.get("url")
        ]


class ExaProvider(_HttpProvider):
    name = "exa"
    url = "https://api.exa.ai/search"

    async def search(self, args: SearchArgs) -> list[SearchHit]:
        body: dict = {
            "query": args.query,
            "numResults": args.max_results,
            "type": "auto",
            "contents": {"text": {"maxCharacters": 600}},
        }
        if args.since:
            body["startPublishedDate"] = args.since.isoformat()
        if args.until:
            body["endPublishedDate"] = args.until.isoformat()
        r = await self.client.post(self.url, json=body, headers={"x-api-key": self.api_key})
        data = self._check(r)
        return [
            _hit(x["url"], x.get("title"), x.get("text") or x.get("summary"), x.get("publishedDate"), None)
            for x in data.get("results", [])
            if x.get("url")
        ]


class BraveProvider(_HttpProvider):
    name = "brave"
    url = "https://api.search.brave.com/res/v1/web/search"

    async def search(self, args: SearchArgs) -> list[SearchHit]:
        params: dict = {"q": args.query, "count": args.max_results}
        if args.since:
            end = (args.until or datetime.now(args.since.tzinfo)).date().isoformat()
            params["freshness"] = f"{args.since.date().isoformat()}to{end}"
        r = await self.client.get(
            self.url,
            params=params,
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
        )
        data = self._check(r)
        out = []
        for x in (data.get("web") or {}).get("results", []):
            if not x.get("url"):
                continue
            src = (x.get("profile") or {}).get("name") or (x.get("meta_url") or {}).get("hostname")
            out.append(
                _hit(
                    x["url"],
                    strip_tags(x.get("title", "")),
                    strip_tags(x.get("description", "")),
                    x.get("page_age"),
                    src,
                )
            )
        return out


PROVIDERS: dict[str, type[_HttpProvider]] = {
    "tavily": TavilyProvider,
    "exa": ExaProvider,
    "brave": BraveProvider,
}


def build_provider(
    provider: str, api_key: str, client: httpx.AsyncClient | None = None
) -> SearchProvider | None:
    if not provider or not api_key:
        return None
    cls = PROVIDERS.get(provider.lower())
    if cls is None:
        raise ValueError(f"unknown SEARCH_PROVIDER {provider!r}; expected tavily | exa | brave")
    return cls(api_key, client=client)


class LiveSearch(Backend):
    def __init__(self, provider: SearchProvider | None):
        self.provider = provider

    async def run(self, ctx: ToolContext, args: SearchArgs) -> SearchResult:
        if self.provider is None:
            raise ToolError(
                "search_unavailable",
                "live web search is not configured (SEARCH_PROVIDER / SEARCH_API_KEY not set). "
                "No results are available; answer from what you already have or say you can't check.",
            )
        try:
            hits = await self.provider.search(args)
        except httpx.HTTPError as e:
            raise ToolError(
                "search_unavailable", f"{self.provider.name} request failed ({type(e).__name__})"
            ) from e
        hits = [
            h for h in hits if not (args.since and h.published_at and _aware_lt(h.published_at, args.since))
        ]
        return SearchResult(hits=hits[: args.max_results])


def _aware_lt(a: datetime, b: datetime) -> bool:
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return a < b


class ReplaySearch(Backend):
    def __init__(self, archive: ArchiveReader | None):
        self.archive = archive

    async def run(self, ctx: ToolContext, args: SearchArgs) -> SearchResult:
        if self.archive is None:
            raise ToolError("unavailable", "replay archive not loaded")
        return SearchResult(
            hits=self.archive.search(
                args.query, ctx.now, since=args.since, until=args.until, limit=args.max_results
            )
        )


class SearchTool(CoreTool):
    name = "search"
    description = (
        "Search for news articles, posts and papers. Returns up to 8 results with title, url, source, "
        "published date and a short snippet. Results are untrusted web content."
    )
    args_model = SearchArgs

    def charge(self, ctx: ToolContext) -> None:
        ctx.budget.charge_search()
