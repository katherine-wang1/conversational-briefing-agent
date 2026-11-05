"""Tool layer (TDD §5): `search`, `fetch`, `headline_source`, `hn_comments`, each with a live and a
replay backend selected by `ctx.mode`, plus the untrusted-content wrapper.

    tools = build_tools(settings, db=db)              # live + (if the archive exists) replay
    result = await tools["search"](ctx, query="...")  # ToolResult; result.as_untrusted_text() for the model
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.contracts.tools import Tool
from app.tools.archive import ArchiveReader
from app.tools.fetch import (
    DbDocumentCache,
    DocumentCache,
    FetchTool,
    InMemoryDocumentCache,
    LiveFetch,
    ReplayFetch,
)
from app.tools.headlines import (
    HeadlineSourceTool,
    HnCommentsTool,
    LiveHeadlines,
    LiveHnComments,
    ReplayHeadlines,
    ReplayHnComments,
    register_source_adapter,
)
from app.tools.search import LiveSearch, ReplaySearch, SearchTool, build_provider
from app.tools.untrusted import render_for_model, sanitize_text, wrap_untrusted

__all__ = [
    "ArchiveReader",
    "build_tools",
    "register_source_adapter",
    "render_for_model",
    "sanitize_text",
    "wrap_untrusted",
]


def build_tools(
    settings: Any = None,
    *,
    db: Any = None,
    archive: ArchiveReader | str | Path | None = None,
    search_provider: str | None = None,
    search_api_key: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    doc_cache: DocumentCache | None = None,
    check_urls: bool = True,
) -> dict[str, Tool]:
    if settings is None:
        from app.config.settings import get_settings

        settings = get_settings()
    if archive is None and settings.archive_path and Path(settings.archive_path).exists():
        archive = settings.archive_path
    reader = ArchiveReader(archive) if isinstance(archive, str | Path) else archive
    provider = build_provider(
        settings.search_provider if search_provider is None else search_provider,
        settings.search_api_key if search_api_key is None else search_api_key,
        client=http_client,
    )
    cache = doc_cache or (DbDocumentCache(db) if db is not None else InMemoryDocumentCache())
    tools: list[Any] = [
        SearchTool(LiveSearch(provider), ReplaySearch(reader)),
        FetchTool(LiveFetch(cache, http_client, check_urls=check_urls), ReplayFetch(reader)),
        HeadlineSourceTool(LiveHeadlines(), ReplayHeadlines(reader)),
        HnCommentsTool(LiveHnComments(http_client), ReplayHnComments(reader)),
    ]
    return {t.name: t for t in tools}
