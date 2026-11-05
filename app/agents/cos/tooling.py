"""Calling `Tool`s (app/contracts/tools.py) from the per-user agents and normalising their results.

Tools are passed in as a mapping `{name: Tool}` (W-E builds the real ones). Results may carry typed
objects (`SearchResult`, `FetchResult`, ...) or their dict dumps; both are accepted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agents.cos.llm import as_uuid
from app.agents.cos.untrusted import wrap
from app.contracts.common import Mode, TokenBudget, TraceCtx
from app.contracts.sources import Comment, Document, SearchHit
from app.contracts.tools import (
    CommentsResult,
    FetchResult,
    SearchResult,
    Tool,
    ToolContext,
    ToolResult,
)


class ToolUnavailable(Exception):
    pass


def tool_ctx(
    *,
    mode: Mode,
    now: datetime,
    run_id: str,
    user_id: str | None,
    budget: TokenBudget,
    role: str,
    step: str,
    job_id: str | None = None,
) -> ToolContext:
    uid = as_uuid(user_id)
    return ToolContext(
        mode=mode,
        now=now,
        run_id=run_id,
        user_id=uid,
        budget=budget,
        trace=TraceCtx(run_id=run_id, user_id=uid, job_id=job_id, role=role, step=step),
    )


def _data(res: ToolResult) -> Any:
    if not res.ok:
        raise ToolUnavailable(res.error or f"{res.tool} failed")
    return res.data


async def search(
    tools: dict[str, Tool],
    ctx: ToolContext,
    query: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    max_results: int = 8,
) -> list[SearchHit]:
    tool = tools.get("search")
    if tool is None:
        raise ToolUnavailable("no search tool")
    data = _data(await tool(ctx, query=query, since=since, until=until, max_results=max_results))
    if isinstance(data, SearchResult):
        return list(data.hits)
    if isinstance(data, dict):
        return list(SearchResult.model_validate(data).hits)
    if isinstance(data, list):
        return [h if isinstance(h, SearchHit) else SearchHit.model_validate(h) for h in data]
    raise ToolUnavailable(f"unexpected search result type {type(data).__name__}")


async def fetch(
    tools: dict[str, Tool],
    ctx: ToolContext,
    *,
    url: str | None = None,
    doc_id: str | None = None,
    max_tokens: int = 3000,
) -> Document | None:
    tool = tools.get("fetch")
    if tool is None:
        raise ToolUnavailable("no fetch tool")
    kwargs: dict[str, Any] = {"max_tokens": max_tokens}
    if doc_id:
        kwargs["doc_id"] = doc_id
    if url:
        kwargs["url"] = url
    data = _data(await tool(ctx, **kwargs))
    if data is None:
        return None
    if isinstance(data, FetchResult):
        return data.document
    if isinstance(data, Document):
        return data
    if isinstance(data, dict):
        if "document" in data:
            return FetchResult.model_validate(data).document
        return Document.model_validate(data)
    raise ToolUnavailable(f"unexpected fetch result type {type(data).__name__}")


async def hn_comments(
    tools: dict[str, Tool], ctx: ToolContext, item_id: str, limit: int = 5
) -> list[Comment]:
    tool = tools.get("hn_comments")
    if tool is None:
        raise ToolUnavailable("no hn_comments tool")
    data = _data(await tool(ctx, item_id=item_id, limit=limit))
    if isinstance(data, CommentsResult):
        return list(data.comments)
    if isinstance(data, dict):
        return list(CommentsResult.model_validate(data).comments)
    if isinstance(data, list):
        return [c if isinstance(c, Comment) else Comment.model_validate(c) for c in data]
    raise ToolUnavailable(f"unexpected hn_comments result type {type(data).__name__}")


def render_hits(hits: list[SearchHit]) -> str:
    """Compact, wrapped search results for a model (title, source, date, snippet)."""
    if not hits:
        return "(no results)"
    out = []
    for h in hits:
        date = h.published_at.date().isoformat() if h.published_at else "unknown date"
        out.append(wrap(f"{h.title}\n{h.source or ''} · {date}\n{h.snippet}", id=h.doc_id, url=h.url))
    return "\n".join(out)
