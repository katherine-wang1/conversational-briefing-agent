"""Newsroom run trace (NEWS-5): steps, prompt hashes, model and tool calls, token usage.

The trace is the second return value of `run_newsroom`. It also carries the run's `Story` objects,
which the caller stores and passes back as `history` on the next run (story identity).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.contracts.common import Strict
from app.contracts.digest import Story


class LlmCallRecord(Strict):
    step: str
    role: str
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    model: str | None = None
    provider: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: float | None = None
    cached: bool = False
    latency_ms: int = 0
    ok: bool = True
    error: str | None = None
    output: dict[str, Any] | None = None  # parsed structured output, or tool calls requested


class ToolCallRecord(Strict):
    step: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    error: str | None = None
    result_excerpt: str | None = None  # what was shown to the model (truncated)


class TraceStep(Strict):
    step: str  # "N2".."N6"
    name: str
    detail: dict[str, Any] = {}


class Usage(Strict):
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    fetches: int = 0


class NewsroomTrace(Strict):
    run_id: str
    beat: str
    at: datetime
    version: str
    flags: dict[str, Any]
    settings: dict[str, Any]
    prompt_hashes: dict[str, str]  # prompt id -> sha256
    embedder: str
    steps: list[TraceStep] = []
    llm_calls: list[LlmCallRecord] = []
    tool_calls: list[ToolCallRecord] = []
    usage: Usage = Usage()
    degraded: list[str] = []  # human-readable notes on fallbacks (budget, invalid output, ...)
    stories: list[Story] = []  # every story of this run (not only digest entries)

    def add_llm(self, rec: LlmCallRecord) -> None:
        self.llm_calls.append(rec)
        self.usage.llm_calls += 1
        self.usage.tokens_in += rec.tokens_in
        self.usage.tokens_out += rec.tokens_out
        self.usage.tokens_cached += rec.tokens_cached
        self.usage.cost_usd += rec.cost_usd or 0.0


Trace = NewsroomTrace
