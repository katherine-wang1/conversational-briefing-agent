"""Tool interface (TDD §5). Every tool has a live and a replay backend behind this interface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from app.contracts.common import Mode, Strict, TokenBudget, TraceCtx
from app.contracts.sources import Comment, Document, HeadlineObservation, SearchHit


class ToolSpec(Strict):
    """JSON-schema description shown to the model (OpenAI function-calling shape)."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolContext(Strict):
    mode: Mode
    now: datetime  # wall clock (live) or simulated (replay)
    run_id: str = "live"
    user_id: UUID | None = None  # None for newsroom
    budget: TokenBudget
    trace: TraceCtx
    # True when the caller already charged `budget` for this call (e.g. the newsroom charges
    # `charge_fetch()` itself before invoking `fetch`); the tool then does not charge again.
    budget_precharged: bool = False


class ToolResult(Strict):
    tool: str
    ok: bool = True
    error: str | None = None
    data: Any = None

    def as_untrusted_text(self) -> str:
        """Render for the model, wrapped as untrusted content (TDD §5.3).

        Concrete tools should override formatting via `render_untrusted` helpers in app/tools;
        this default keeps the wrapper consistent.
        """
        body = self.error if not self.ok else str(self.data)
        return f'<untrusted_source tool="{self.tool}">\n{body}\n</untrusted_source>'


@runtime_checkable
class Tool(Protocol):
    name: str
    spec: ToolSpec

    async def __call__(self, ctx: ToolContext, **kwargs: Any) -> ToolResult: ...


# ---- Typed results for the core tools ---------------------------------------------------------


class SearchArgs(Strict):
    query: str
    since: datetime | None = None
    until: datetime | None = None
    max_results: int = Field(default=8, ge=1, le=8)


class SearchResult(Strict):
    hits: list[SearchHit]


class FetchArgs(Strict):
    url: str | None = None
    doc_id: str | None = None
    max_tokens: int = 3000


class FetchResult(Strict):
    document: Document | None


class HeadlineSourceArgs(Strict):
    source_id: str
    at: datetime


class HeadlineSourceResult(Strict):
    observations: list[HeadlineObservation]


class CommentsArgs(Strict):
    item_id: str
    limit: int = 5


class CommentsResult(Strict):
    comments: list[Comment]


CORE_TOOL_NAMES = Literal["search", "fetch", "headline_source", "hn_comments"]
