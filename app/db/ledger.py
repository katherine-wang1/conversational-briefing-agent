"""Postgres sink for the W-B cost ledger (table `llm_calls`, TDD §4.6 / §10.2).

W-B's ledger writes one record per model call through a sink interface; this is the Postgres
implementation. Records are duck-typed (a pydantic model or a dict) with the fields below, so it
plugs into whatever record class `app/llm/ledger.py` defines. Rows are written from a system scope:
users can never read the ledger back (RLS), only the service and app_admin can.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.pool import Database

log = logging.getLogger(__name__)

FIELDS = (
    "run_id", "user_id", "job_id", "role", "provider", "model", "tokens_in", "tokens_out",
    "tokens_cached", "cost_usd", "latency_ms", "cached",
)  # fmt: skip


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
