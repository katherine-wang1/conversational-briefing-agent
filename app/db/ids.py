"""Agent artifact ids are ULIDs (text), optionally prefixed (TDD §10.1)."""

from __future__ import annotations

from ulid import ULID


def new_id(prefix: str = "") -> str:
    u = str(ULID()).lower()
    return f"{prefix}_{u}" if prefix else u
