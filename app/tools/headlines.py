"""`headline_source` and `hn_comments` tools (TDD §5.2).

headline_source, live: dispatches to the source parsers in `app/sources` (W-A). Adapters register
by type (`register_source_adapter("rss", fn)`); on first use we also import `app.sources` and pick
up its `ADAPTERS` dict or `register_adapters(register)` hook, whichever it provides. A source
type without an adapter returns an `unavailable` error (not empty results).
Replay: the latest archived snapshot of that source at or before `at` (and never after ctx.now).
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from app.config import loader
from app.config.loader import HeadlineSourceCfg
from app.contracts.sources import Comment, HeadlineObservation
from app.contracts.tools import (
    CommentsArgs,
    CommentsResult,
    HeadlineSourceArgs,
    HeadlineSourceResult,
    ToolContext,
)
from app.tools.archive import ArchiveReader
from app.tools.base import Backend, CoreTool, ToolError
from app.tools.untrusted import strip_tags

SourceAdapter = Callable[[HeadlineSourceCfg, str, datetime], Awaitable[list[HeadlineObservation]]]
_ADAPTERS: dict[str, SourceAdapter] = {}
_discovered = False


def register_source_adapter(source_type: str, fn: SourceAdapter) -> None:
    """fn(cfg, beat_id, at) -> observations as currently visible."""
    _ADAPTERS[source_type] = fn


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    _discovered = True
    try:
        mod = importlib.import_module("app.sources")
    except ImportError:
        return
    for k, fn in (getattr(mod, "ADAPTERS", None) or {}).items():
        _ADAPTERS.setdefault(k, fn)
    hook = getattr(mod, "register_adapters", None)
    if callable(hook):
        hook(register_source_adapter)


def source_config(source_id: str) -> tuple[str, HeadlineSourceCfg]:
    for beat in loader.beats().values():
        for s in beat.headline_sources:
            if s.id == source_id:
                return beat.id, s
    raise ToolError("not_found", f"unknown source_id {source_id!r}")


def _clip(at: datetime, now: datetime) -> datetime:
    return min(at, now)


class LiveHeadlines(Backend):
    async def run(self, ctx: ToolContext, args: HeadlineSourceArgs) -> HeadlineSourceResult:
        _discover()
        beat, cfg = source_config(args.source_id)
        fn = _ADAPTERS.get(cfg.type)
        if fn is None:
            raise ToolError("unavailable", f"no live adapter for source type {cfg.type!r} (app/sources)")
        obs = await fn(cfg, beat, _clip(args.at, ctx.now))
        return HeadlineSourceResult(observations=[o for o in obs if o.observed_at <= ctx.now])


class ReplayHeadlines(Backend):
    def __init__(self, archive: ArchiveReader | None):
        self.archive = archive

    async def run(self, ctx: ToolContext, args: HeadlineSourceArgs) -> HeadlineSourceResult:
        if self.archive is None:
            raise ToolError("unavailable", "replay archive not loaded")
        return HeadlineSourceResult(
            observations=self.archive.observations(args.source_id, _clip(args.at, ctx.now))
        )


class HeadlineSourceTool(CoreTool):
    name = "headline_source"
    description = "Headlines visible on a configured source (front page / feed) at a time, with rank."
    args_model = HeadlineSourceArgs


# ---- hn_comments ------------------------------------------------------------------------------------


class LiveHnComments(Backend):
    url = "https://hn.algolia.com/api/v1/items/{id}"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=8.0)

    async def run(self, ctx: ToolContext, args: CommentsArgs) -> CommentsResult:
        try:
            r = await self.client.get(self.url.format(id=args.item_id))
        except httpx.HTTPError as e:
            raise ToolError("unavailable", f"HN request failed ({type(e).__name__})") from e
        if r.status_code == 404:
            raise ToolError("not_found", f"HN item {args.item_id} not found")
        if r.status_code >= 400:
            raise ToolError("unavailable", f"HN returned HTTP {r.status_code}")
        out = []
        for c in r.json().get("children", []):
            if not c.get("text") or not c.get("created_at"):
                continue
            created = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
            if created > ctx.now:
                continue
            out.append(
                Comment(
                    item_id=str(c.get("id")),
                    created_at=created,
                    text=strip_tags(c["text"])[:600],
                    points=c.get("points"),
                )
            )
            if len(out) >= args.limit:
                break
        return CommentsResult(comments=out)


class ReplayHnComments(Backend):
    def __init__(self, archive: ArchiveReader | None):
        self.archive = archive

    async def run(self, ctx: ToolContext, args: CommentsArgs) -> CommentsResult:
        if self.archive is None:
            raise ToolError("unavailable", "replay archive not loaded")
        return CommentsResult(comments=self.archive.comments(args.item_id, ctx.now, args.limit))


class HnCommentsTool(CoreTool):
    name = "hn_comments"
    description = "Top Hacker News comments on an item (as of now), text truncated. Untrusted content."
    args_model = CommentsArgs
