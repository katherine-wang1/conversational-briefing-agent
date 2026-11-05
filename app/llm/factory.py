"""Process-level wiring of the model layer: `get_client()` builds the production `Router`.

Every live call is written to the JSONL ledger ($LLM_LEDGER_PATH) and, when a database is given, to
the Postgres `llm_calls` table (W-E's `PostgresLedgerSink` behind a sync bridge). `LLM_SPEND_CAP_USD`
sets the router's hard spend fence for the process (no cap when unset).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.llm.ledger import JsonlLedgerSink, Ledger, LedgerSink
from app.llm.router import Router

log = logging.getLogger(__name__)


def _env_cap() -> float | None:
    raw = os.environ.get("LLM_SPEND_CAP_USD", "").strip()
    return float(raw) if raw else None


def db_ledger_sink(db: Any) -> LedgerSink:
    from app.db.ledger import BackgroundLedgerSink, PostgresLedgerSink

    return BackgroundLedgerSink(PostgresLedgerSink(db))


def attach_db_ledger(client: Any, db: Any) -> bool:
    """Add the `llm_calls` sink to an existing Router (e.g. once the web process has opened its pool)."""
    ledger = getattr(client, "ledger", None)
    if not isinstance(ledger, Ledger) or db is None:
        return False
    from app.db.ledger import BackgroundLedgerSink

    if any(isinstance(s, BackgroundLedgerSink) for s in ledger.sinks):
        return False
    ledger.sinks.append(db_ledger_sink(db))
    return True


async def flush_ledger(client: Any) -> None:
    from app.db.ledger import BackgroundLedgerSink

    for s in getattr(getattr(client, "ledger", None), "sinks", []):
        if isinstance(s, BackgroundLedgerSink):
            await s.flush()


def get_client(
    db: Any = None,
    *,
    spend_cap_usd: float | None = None,
    ledger_path: str | None = None,
    require_key: bool = True,
) -> Router | None:
    """The `ModelClient` for agents. Returns None (with a warning) when no provider key is set, so
    callers can fall back to their placeholder paths instead of failing on the first call."""
    if require_key and not os.environ.get("OPENROUTER_API_KEY"):
        log.warning("OPENROUTER_API_KEY not set: no model client (agents needing a model will fail)")
        return None
    path = ledger_path or os.environ.get("LLM_LEDGER_PATH", "eval/runs/llm_ledger.jsonl")
    sinks: list[LedgerSink] = [JsonlLedgerSink(path)]
    if db is not None:
        sinks.append(db_ledger_sink(db))
    cap = spend_cap_usd if spend_cap_usd is not None else _env_cap()
    return Router.from_env(ledger=Ledger(sinks, spend_cap_usd=cap))
