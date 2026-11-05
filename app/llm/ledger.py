"""Cost ledger (TDD §4.6, §21): one record per model call.

The ledger writes through a `LedgerSink`. The JSONL sink is the default; W-E can add a Postgres
`llm_calls` sink by implementing the same one-method protocol.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from app.contracts.common import Strict
from app.llm.errors import SpendCapExceeded

CallKind = Literal["call", "repair", "cache_hit"]


class LedgerRecord(Strict):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: CallKind = "call"
    role: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: float = 0.0
    cost_source: Literal["provider", "estimated", "none", "cache"] = "provider"
    latency_ms: int = 0
    ttft_ms: int | None = None
    stream: bool = False
    run_id: str = "live"
    user_id: UUID | None = None
    job_id: str | None = None
    step: str | None = None
    cache_namespace: str = "live"
    finish_reason: str | None = None
    ok: bool = True
    error: str | None = None


class LedgerSink(Protocol):
    def write(self, record: LedgerRecord) -> None: ...


class JsonlLedgerSink:
    """Append-only JSONL file. Safe for one process with several async tasks."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: LedgerRecord) -> None:
        line = record.model_dump_json()
        with self._lock, self.path.open("a") as f:
            f.write(line + "\n")

    def read(self) -> list[LedgerRecord]:
        if not self.path.exists():
            return []
        return [LedgerRecord.model_validate(json.loads(x)) for x in self.path.read_text().splitlines() if x]


class MemoryLedgerSink:
    """In-memory sink for tests."""

    def __init__(self) -> None:
        self.records: list[LedgerRecord] = []

    def write(self, record: LedgerRecord) -> None:
        self.records.append(record)


class Ledger:
    """Fans records out to sinks and keeps a running spend total.

    `spend_cap_usd` is an optional hard fence: `check_cap()` raises `SpendCapExceeded` once the total
    recorded spend reaches it. The router calls it before every non-cached call.
    """

    def __init__(self, sinks: list[LedgerSink] | None = None, spend_cap_usd: float | None = None):
        self.sinks: list[LedgerSink] = sinks if sinks is not None else [MemoryLedgerSink()]
        self.spend_cap_usd = spend_cap_usd
        self.total_cost_usd = 0.0
        self.n_calls = 0

    def record(self, rec: LedgerRecord) -> None:
        self.total_cost_usd += rec.cost_usd
        self.n_calls += 1
        for s in self.sinks:
            s.write(rec)

    def check_cap(self) -> None:
        if self.spend_cap_usd is not None and self.total_cost_usd >= self.spend_cap_usd:
            raise SpendCapExceeded(
                f"spend cap ${self.spend_cap_usd:.4f} reached (spent ${self.total_cost_usd:.4f})"
            )
