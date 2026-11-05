"""Convert between memory rows and the memory contracts (app/contracts/memory.py)."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.contracts.memory import DeliveryPref, Interest, MemorySignal, MemorySnapshot
from app.db.scope import Scope

_INTEREST_FIELDS = tuple(Interest.model_fields)


def row_to_interest(r: dict[str, Any]) -> Interest:
    return Interest.model_validate({k: r[k] for k in _INTEREST_FIELDS if k in r})


def row_to_signal(r: dict[str, Any]) -> MemorySignal:
    return MemorySignal(
        session_id=r["session_id"] or "",
        type=r["type"],
        story_ref=r["story_ref"],
        interest_hint=r["interest_hint"],
        strength=r["strength"],
        explicit=r["explicit"],
        turn_id=r["turn_id"],
        payload=r["payload"] or {},
        created_at=r["created_at"],
    )


async def load_memory(s: Scope, as_of: date) -> MemorySnapshot:
    uid = s.require_user()
    interests = [row_to_interest(r) for r in await s.interests.list(run_id=s.run_id)]
    prefs = [
        DeliveryPref(key=r["key"], value=r["value"], confidence=r["confidence"])
        for r in await s.delivery_prefs.list(run_id=s.run_id)
    ]
    return MemorySnapshot(user_id=str(uid), run_id=s.run_id, as_of=as_of, interests=interests, delivery=prefs)


async def save_memory(s: Scope, snap: MemorySnapshot) -> None:
    """Make the interests / delivery_prefs tables equal to `snap` (insert, update, delete)."""
    existing = {r["id"] for r in await s.interests.list(run_id=s.run_id)}
    keep = set()
    for i in snap.interests:
        keep.add(i.id)
        await s.interests.upsert(conflict=("user_id", "run_id", "id"), **i.model_dump())
    for gone in existing - keep:
        await s.interests.delete_where(id=gone, run_id=s.run_id)
    for p in snap.delivery:
        await s.delivery_prefs.set(p.key, p.value, p.confidence)
