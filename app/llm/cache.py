"""LLM call cache (TDD §4.5). SQLite; a hit returns the stored response with `cached=True` at $0.

Key = sha256(provider, model, canonical JSON of messages / tools / schema / params, cache_namespace).
Only final, successful responses are stored (after structured-output validation).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts.llm import ChatRequest, ChatResponse, Usage

DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / "eval" / "cache" / "llm_cache.sqlite"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def cache_key(provider: str, model: str, req: ChatRequest, params: dict[str, Any] | None = None) -> str:
    payload = {
        "provider": provider,
        "model": model,
        "messages": [m.model_dump(mode="json") for m in req.messages],
        "tools": [t.model_dump(mode="json") for t in req.tools] if req.tools else None,
        "schema": req.response_schema.model_json_schema() if req.response_schema else None,
        "params": {
            "max_output_tokens": req.max_output_tokens,
            "temperature": req.temperature,
            **(params or {}),
        },
        "ns": req.cache_namespace,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


class CallCache:
    def __init__(self, path: str | Path = DEFAULT_CACHE_PATH):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache ("
            " key TEXT PRIMARY KEY, namespace TEXT, provider TEXT, model TEXT,"
            " response TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self._db.commit()

    def get(self, key: str) -> ChatResponse | None:
        with self._lock:
            row = self._db.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        resp = ChatResponse.model_validate_json(row[0])
        u = resp.usage
        return resp.model_copy(
            update={
                "cached": True,
                "latency_ms": 0,
                "usage": Usage(tokens_in=u.tokens_in, tokens_out=u.tokens_out,
                               tokens_cached=u.tokens_cached, cost_usd=0.0),
            }
        )

    def put(self, key: str, namespace: str, resp: ChatResponse) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?, ?, ?)",
                (key, namespace, resp.provider, resp.model, resp.model_dump_json(),
                 datetime.now(UTC).isoformat()),
            )
            self._db.commit()

    def __len__(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]

    def close(self) -> None:
        self._db.close()
