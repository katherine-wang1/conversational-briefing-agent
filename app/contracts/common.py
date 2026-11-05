"""Shared primitives used across contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["live", "replay"]


class Strict(BaseModel):
    """Base model: forbid unknown fields so contract drift fails loudly."""

    model_config = ConfigDict(extra="forbid")


class TraceCtx(Strict):
    """Identity and provenance attached to every model and tool call."""

    run_id: str = "live"
    user_id: UUID | None = None  # None for shared (newsroom) work
    job_id: str | None = None
    role: str | None = None  # logical role, e.g. "cos.score"
    step: str | None = None  # free-form step label, e.g. "C4"


class BudgetExceeded(Exception):
    """Raised when a call would exceed the run's budget."""


class TokenBudget(Strict):
    """Per-run budget. Agents must catch BudgetExceeded and degrade gracefully."""

    input_tokens: int
    output_tokens: int | None = None
    searches: int | None = None
    fetches: int | None = None
    used_input_tokens: int = 0
    used_output_tokens: int = 0
    used_searches: int = 0
    used_fetches: int = 0

    def charge_tokens(self, tokens_in: int, tokens_out: int = 0) -> None:
        if self.used_input_tokens + tokens_in > self.input_tokens:
            raise BudgetExceeded(f"input token budget {self.input_tokens} exceeded")
        if self.output_tokens is not None and self.used_output_tokens + tokens_out > self.output_tokens:
            raise BudgetExceeded(f"output token budget {self.output_tokens} exceeded")
        self.used_input_tokens += tokens_in
        self.used_output_tokens += tokens_out

    def charge_search(self) -> None:
        if self.searches is not None and self.used_searches >= self.searches:
            raise BudgetExceeded(f"search budget {self.searches} exceeded")
        self.used_searches += 1

    def charge_fetch(self) -> None:
        if self.fetches is not None and self.used_fetches >= self.fetches:
            raise BudgetExceeded(f"fetch budget {self.fetches} exceeded")
        self.used_fetches += 1


class Citation(Strict):
    passage_id: str


class Timestamped(Strict):
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
