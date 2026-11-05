"""Postgres sink for the W-B cost ledger (table `llm_calls`, TDD §4.6 / §10.2).

W-B's ledger writes one record per model call through a sink interface; this is the Postgres
implementation. Records are duck-typed (a pydantic model or a dict) with the fields below, so it
plugs into whatever record class `app/llm/ledger.py` defines. Rows are written from a system scope:
users can never read the ledger back (RLS), only the service and app_admin can.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.pool import Database

log = logging.getLogger(__name__)

FIELDS = (
    "run_id",
    "user_id",
    "job_id",
    "role",
    "provider",
    "model",
    "tokens_in",
    "tokens_out",
    "tokens_cached",
    "cost_usd",
    "latency_ms",
    "cached",
)


def _record_dict(record: Any) -> dict[str, Any]:
    raw = record.model_dump() if isinstance(record, BaseModel) else dict(record)
    trace = raw.get("trace") or {}
    if isinstance(trace, BaseModel):
        trace = trace.model_dump()
    usage = raw.get("usage") or {}
    if isinstance(usage, BaseModel):
        usage = usage.model_dump()
    out: dict[str, Any] = {}
    for f in FIELDS:
        v = raw.get(f, usage.get(f, trace.get(f)))
        if v is not None:
            out[f] = v
    if "user_id" in out and not isinstance(out["user_id"], UUID):
        out["user_id"] = UUID(str(out["user_id"])) if out["user_id"] else None
    if "cached" not in out and raw.get("kind") is not None:
        out["cached"] = raw.get("kind") == "cache_hit"  # W-B LedgerRecord.kind
    out.setdefault("run_id", "live")
    out.setdefault("role", "unknown")
    out.setdefault("provider", "unknown")
    out.setdefault("model", "unknown")
    return out


class PostgresLedgerSink:
    def __init__(self, db: Database):
        self.db = db

    async def write(self, record: Any) -> None:
        row = _record_dict(record)
        async with self.db.system_scope() as s:
            await s.llm_calls.insert(**row)

    async def __call__(self, record: Any) -> None:
        await self.write(record)


class BackgroundLedgerSink:
    """Adapts the async `PostgresLedgerSink` to W-B's synchronous `LedgerSink.write(record)`.

    The router records from inside the running event loop, so each write is scheduled as a task on
    that loop (the model call is not delayed by the insert). Failures are logged, never raised: the
    JSONL sink next to it remains the durable copy. Call `await flush()` before shutdown / in tests.
    Without a running loop (sync callers) the insert runs to completion on a fresh loop.
    """

    def __init__(self, sink: PostgresLedgerSink):
        self.sink = sink
        self._pending: set[asyncio.Task[None]] = set()
        self.failures = 0

    async def _write(self, record: Any) -> None:
        try:
            await self.sink.write(record)
        except Exception:  # noqa: BLE001 - the ledger must never fail a model call
            self.failures += 1
            log.exception("llm_calls insert failed (record kept in the JSONL ledger)")

    def write(self, record: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._write(record))
            return
        task = loop.create_task(self._write(record))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def flush(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
