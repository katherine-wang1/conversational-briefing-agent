"""Model layer (TDD §4). Agents depend only on `app.contracts.llm.ModelClient`.

- `Router` (the ModelClient for production code): role -> provider/model, cache, budgets, ledger, repair.
- `OpenRouterClient`, `TinkerClient`: provider transports over httpx.
- `FakeModelClient`: scripted client for tests.
- `get_client(db)`: the process's Router (JSONL + Postgres `llm_calls` ledger, $LLM_SPEND_CAP_USD fence).
"""

from app.llm.cache import CallCache, cache_key
from app.llm.errors import (
    LLMError,
    ProviderError,
    RetryableProviderError,
    SpendCapExceeded,
    StructuredOutputError,
)
from app.llm.factory import attach_db_ledger, flush_ledger, get_client
from app.llm.fake import FakeModelClient, FakeReply
from app.llm.ledger import JsonlLedgerSink, Ledger, LedgerRecord, LedgerSink, MemoryLedgerSink
from app.llm.openrouter import OpenRouterCatalog, OpenRouterClient
from app.llm.router import BoundModelClient, Router
from app.llm.tinker import TinkerClient

__all__ = [
    "BoundModelClient", "CallCache", "FakeModelClient", "FakeReply", "JsonlLedgerSink", "LLMError", "Ledger",
    "LedgerRecord", "LedgerSink", "MemoryLedgerSink", "OpenRouterCatalog", "OpenRouterClient",
    "ProviderError", "RetryableProviderError", "Router", "SpendCapExceeded", "StructuredOutputError",
    "TinkerClient", "attach_db_ledger", "cache_key", "flush_ledger", "get_client",
]
