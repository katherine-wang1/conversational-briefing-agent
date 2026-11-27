"""`fetch` tool (TDD §5.2, §5.4).

Live: httpx + hidden-text stripping + trafilatura, cached in the shared `documents` table by url_hash.
Google News redirect links are resolved to the publisher's URL first (live only; FetchResult.resolved_url).
Replay: archived text as of ≤ ctx.now.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.contracts.sources import Document
from app.contracts.tools import FetchArgs, FetchResult, ToolContext
from app.sources.google_news import GoogleNewsResolver, is_google_news_url
from app.tools.archive import ArchiveReader
from app.tools.base import Backend, CoreTool, ToolError
from app.tools.untrusted import sanitize_text, strip_hidden_html
from app.tools.urls import BlockedURL, canonical_url, check_public_http_url, hostname, url_hash

USER_AGENT = "BriefingAgent/0.1 (research prototype; contact via project page)"
MAX_BYTES = 3_000_000
CHARS_PER_TOKEN = 4


class DocumentCache(Protocol):
    async def get(self, url_hash: str) -> Document | None: ...

    async def put(self, doc: Document) -> None: ...


class InMemoryDocumentCache:
    def __init__(self) -> None:
        self.docs: dict[str, Document] = {}

    async def get(self, url_hash: str) -> Document | None:
        return self.docs.get(url_hash)

    async def put(self, doc: Document) -> None:
        self.docs[doc.url_hash] = doc


class DbDocumentCache:
    """The shared `documents` table (no user data; written from any scope)."""

    def __init__(self, db) -> None:  # noqa: ANN001 - app.db.pool.Database
        self.db = db

    async def get(self, h: str) -> Document | None:
        async with self.db.system_scope() as s:
            r = await s.documents.get(h)
        if r is None:
            return None
        return Document(
            url_hash=r["url_hash"],
            url=r["url"],
            canonical_url=r["canonical_url"],
            source=r["source"],
            title=r["title"],
            published_at=r["published_at"],
            available_at=r["available_at"] or r["fetched_at"],
            fetched_at=r["fetched_at"],
            text=r["text"],
            extractor_ver=r["extractor_ver"],
        )

    async def put(self, doc: Document) -> None:
        async with self.db.system_scope() as s:
            await s.documents.upsert(**doc.model_dump())


def extract(raw_html: str, url: str) -> tuple[str | None, str, datetime | None, str]:
    """(title, text, published_at, extractor_ver) from HTML, hidden content removed first."""
    import json

    import trafilatura

    cleaned = strip_hidden_html(raw_html)
    out = trafilatura.extract(
        cleaned,
        url=url,
        output_format="json",
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    ver = f"trafilatura-{trafilatura.__version__}"
    if not out:
        return None, "", None, ver
    j = json.loads(out)
    published = None
    if j.get("date"):
        try:
            published = datetime.fromisoformat(j["date"]).replace(tzinfo=UTC)
        except ValueError:
            published = None
    return j.get("title"), sanitize_text(j.get("text") or ""), published, ver


def truncate(doc: Document, max_tokens: int) -> Document:
    limit = max_tokens * CHARS_PER_TOKEN
    return (
        doc if len(doc.text) <= limit else doc.model_copy(update={"text": doc.text[:limit] + " …[truncated]"})
    )


class LiveFetch(Backend):
    """Live fetch. A Google News redirect link is first resolved to the publisher's URL
    (`app.sources.google_news`); the document is then cached under both the requested URL's hash (so a
    repeat fetch, and its `doc_id`, work as before) and the publisher URL's hash."""

    def __init__(
        self,
        cache: DocumentCache,
        client: httpx.AsyncClient | None = None,
        *,
        check_urls: bool = True,
        timeout_s: float = 10.0,
        resolver: GoogleNewsResolver | None = None,
    ):
        self.cache = cache
        self.client = client or httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self.check_urls = check_urls
        self.resolver = resolver or GoogleNewsResolver(self.client, timeout_s=timeout_s)

    async def run(self, ctx: ToolContext, args: FetchArgs) -> FetchResult:
        if args.doc_id and not args.url:
            doc = await self.cache.get(args.doc_id)
            if doc is None:
                raise ToolError("not_found", f"unknown doc_id {args.doc_id}; fetch by url instead")
            return self._from_cache(doc, args.max_tokens)
        if not args.url:
            raise ToolError("invalid_args", "pass url or doc_id")
        h = url_hash(args.url)
        cached = await self.cache.get(h)
        if cached is not None:
            return self._from_cache(cached, args.max_tokens)
        if not is_google_news_url(args.url):
            doc = await self._fetch(args.url, h)
            await self.cache.put(doc)
            return FetchResult(document=truncate(doc, args.max_tokens))
        res = await self.resolver.resolve(args.url)
        if not res.resolved_url:
            raise ToolError(
                "unavailable",
                f"Google News link not resolved to the publisher's URL ({res.detail or 'unknown reason'})",
            )
        target = res.resolved_url
        th = url_hash(target)
        doc = await self.cache.get(th)
        if doc is None:
            doc = await self._fetch(target, th)
            await self.cache.put(doc)
        doc = doc.model_copy(update={"url_hash": h, "url": args.url})
        await self.cache.put(doc)
        return FetchResult(
            document=truncate(doc, args.max_tokens), resolved_url=target, resolved_via=res.method
        )

    @staticmethod
    def _from_cache(doc: Document, max_tokens: int) -> FetchResult:
        """A cached Google News document carries the publisher URL as its canonical_url."""
        if is_google_news_url(doc.url) and doc.canonical_url and not is_google_news_url(doc.canonical_url):
            return FetchResult(
                document=truncate(doc, max_tokens), resolved_url=doc.canonical_url, resolved_via="cache"
            )
        return FetchResult(document=truncate(doc, max_tokens))

    async def _fetch(self, url: str, h: str) -> Document:
        if self.check_urls:
            try:
                await asyncio.to_thread(check_public_http_url, url)
            except BlockedURL as e:
                raise ToolError("blocked", str(e)) from e
        try:
            r = await self.client.get(url)
        except httpx.HTTPError as e:
            raise ToolError("unavailable", f"fetch failed ({type(e).__name__})") from e
        if r.status_code >= 400:
            raise ToolError("unavailable", f"HTTP {r.status_code}")
        if len(r.content) > MAX_BYTES:
            raise ToolError("unavailable", "page too large")
        ctype = r.headers.get("content-type", "")
        if "html" in ctype or not ctype:
            title, text, published, ver = await asyncio.to_thread(extract, r.text, str(r.url))
        elif ctype.startswith("text/"):
            title, text, published, ver = None, sanitize_text(r.text), None, "plain"
        else:
            raise ToolError("unavailable", f"unsupported content type {ctype.split(';')[0]}")
        now = datetime.now(UTC)
        return Document(
            url_hash=h,
            url=url,
            canonical_url=canonical_url(str(r.url)),
            source=hostname(url),
            title=title,
            published_at=published,
            available_at=now,
            fetched_at=now,
            text=text,
            extractor_ver=ver,
        )


class ReplayFetch(Backend):
    def __init__(self, archive: ArchiveReader | None):
        self.archive = archive

    async def run(self, ctx: ToolContext, args: FetchArgs) -> FetchResult:
        if self.archive is None:
            raise ToolError("unavailable", "replay archive not loaded")
        if not (args.url or args.doc_id):
            raise ToolError("invalid_args", "pass url or doc_id")
        doc = self.archive.document(ctx.now, doc_id=args.doc_id, url=None if args.doc_id else args.url)
        if doc is None and args.url:
            doc = self.archive.document(ctx.now, doc_id=url_hash(args.url))
        return FetchResult(document=truncate(doc, args.max_tokens) if doc else None)


class FetchTool(CoreTool):
    name = "fetch"
    description = (
        "Fetch the main text of an article by url (or doc_id from search). Returns untrusted content."
    )
    args_model = FetchArgs

    def charge(self, ctx: ToolContext) -> None:
        ctx.budget.charge_fetch()
