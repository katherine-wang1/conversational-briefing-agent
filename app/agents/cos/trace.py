"""Run trace for the per-user agents (COS-8). The job handler saves it alongside the outputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    step: str  # e.g. "C1", "C2", "memory.rules"
    kind: str  # "llm" | "tool" | "code" | "degrade"
    role: str | None = None
    ok: bool = True
    detail: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunTrace(BaseModel):
    run_id: str = "live"
    user_id: str | None = None
    job: str = "cos_daily"
    arm: str = "full"
    events: list[TraceEvent] = Field(default_factory=list)
    prompts: dict[str, str] = Field(default_factory=dict)  # prompt name -> sha

    def add(self, step: str, kind: str, *, role: str | None = None, ok: bool = True, **detail: Any) -> None:
        self.events.append(TraceEvent(step=step, kind=kind, role=role, ok=ok, detail=detail))

    def degraded(self, step: str, reason: str, **detail: Any) -> None:
        self.add(step, "degrade", ok=False, reason=reason, **detail)

    def of(self, step: str, kind: str | None = None) -> list[TraceEvent]:
        return [e for e in self.events if e.step == step and (kind is None or e.kind == kind)]

    @property
    def degradations(self) -> list[TraceEvent]:
        return [e for e in self.events if e.kind == "degrade"]
