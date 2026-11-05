"""Model layer interface (TDD §4). Provider-agnostic; roles map to models in config/models.yaml."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from app.contracts.common import Strict, TokenBudget, TraceCtx
from app.contracts.tools import ToolSpec


class ToolCall(Strict):
    id: str
    name: str
    arguments: dict[str, Any]


class Message(Strict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # for role == "tool"
    cache_breakpoint: bool = False  # hint: end of a stable prefix (prompt caching)


class Usage(Strict):
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: float | None = None  # provider-reported if available


class ChatRequest(BaseModel):
    """Not Strict: carries a Python type (response_schema)."""

    role: str
    messages: list[Message]
    tools: list[ToolSpec] | None = None
    response_schema: type[BaseModel] | None = None
    max_output_tokens: int = 2048
    temperature: float | None = None
    budget: TokenBudget
    trace: TraceCtx
    cache_namespace: str = "live"


class ChatResponse(Strict):
    model: str
    provider: str
    content: str = ""
    tool_calls: list[ToolCall] = []
    parsed: dict[str, Any] | None = None  # validated structured output (model_dump)
    usage: Usage = Usage()
    latency_ms: int = 0
    cached: bool = False  # served from the call cache
    finish_reason: str | None = None


class StreamEvent(Strict):
    type: Literal["text_delta", "tool_call", "done", "error"]
    text: str | None = None
    tool_call: ToolCall | None = None
    response: ChatResponse | None = None  # on "done"
    error: str | None = None


class ModelClient(Protocol):
    async def chat(self, req: ChatRequest) -> ChatResponse: ...

    def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]: ...
