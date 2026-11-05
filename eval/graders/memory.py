"""T4 code graders (TDD §18.2): interest recovery (Spearman, NDCG@5, drift lag) and over-narrowing."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from app.contracts.memory import MemorySnapshot
from eval.graders.mathutil import entropy, normalise, spearman
from eval.sim.profile import SimProfile, ai_subtopic_ids


def learned_weights(snapshot: MemorySnapshot, universe: set[str] | None = None) -> dict[str, float]:
    """Aggregate memory interests to taxonomy weights: sum of interest weights per subtopic,
    restricted to `universe` (default: AI-beat subtopics), normalised to 1."""
    universe = universe or ai_subtopic_ids()
    agg: dict[str, float] = {s: 0.0 for s in universe}
    for i in snapshot.interests:
        if i.subtopic in agg:
            agg[i.subtopic] += i.weight
    return normalise(agg)


def _vec(w: Mapping[str, float], keys: Sequence[str]) -> list[float]:
    return [w.get(k, 0.0) for k in keys]


def spearman_recovery(learned: Mapping[str, float], hidden: Mapping[str, float],
                      universe: set[str] | None = None) -> float | None:
    keys = sorted(universe or ai_subtopic_ids())
    return spearman(_vec(learned, keys), _vec(hidden, keys))


def ndcg_at_k(learned: Mapping[str, float], hidden: Mapping[str, float], k: int = 5,
              universe: set[str] | None = None) -> float | None:
    """NDCG@k of the learned ranking with hidden weights as graded relevance (linear gain).
    Ties in the learned ranking are broken by subtopic id for determinism."""
    keys = sorted(universe or ai_subtopic_ids())
    ranking = sorted(keys, key=lambda s: (-learned.get(s, 0.0), s))[:k]
    dcg = sum(hidden.get(s, 0.0) / math.log2(i + 2) for i, s in enumerate(ranking))
    ideal = sorted((hidden.get(s, 0.0) for s in keys), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else None


class RecoveryDay(BaseModel):
    day_index: int
    spearman: float | None
    ndcg5: float | None


def recovery_trajectory(snapshots: Mapping[int, MemorySnapshot], profile: SimProfile) -> list[RecoveryDay]:
    """Per-day Spearman and NDCG@5 between learned and hidden weights. `snapshots` maps window day
    index -> end-of-day snapshot; hidden weights are taken at the same day."""
    out = []
    for d in sorted(snapshots):
        lw = learned_weights(snapshots[d])
        hw = profile.weights_at(d)
        out.append(RecoveryDay(day_index=d, spearman=spearman_recovery(lw, hw), ndcg5=ndcg_at_k(lw, hw)))
    return out


class DriftLag(BaseModel):
    event_day: int
    subtopic: str
    target: float
    start: float  # learned weight the day before the event
    threshold: float
    lag_days: int | None  # None = never crossed within the window (censored)


def drift_lags(snapshots: Mapping[int, MemorySnapshot], profile: SimProfile) -> list[DriftLag]:
    """Drift lag = days from a drift event until the learned weight crosses 50% of the way to the
    target (TDD: '50% of the target'; identical when the start weight is 0, which is the usual case
    for an added interest). For fades/removals the target is 0, so the threshold is half the
    pre-event learned weight. Weights are normalised over AI subtopics."""
    learned = {d: learned_weights(s) for d, s in snapshots.items()}
    days = sorted(learned)
    out = []
    for ev_day, sub, target in profile.drift_targets():
        prev = [d for d in days if d < ev_day]
        start = learned[prev[-1]][sub] if prev else 0.0
        if math.isclose(target, start, abs_tol=1e-9):
            continue  # no movement required (e.g. an unchanged subtopic in a shift)
        thr = start + 0.5 * (target - start)
        up = target > start
        lag = None
        for d in (d for d in days if d >= ev_day):
            v = learned[d][sub]
            if (up and v >= thr) or (not up and v <= thr):
                lag = d - ev_day
                break
        out.append(DriftLag(event_day=ev_day, subtopic=sub, target=target, start=start, threshold=thr, lag_days=lag))
    return out


def over_narrowing(learned: Mapping[str, float], hidden: Mapping[str, float]) -> float | None:
    """entropy(learned) / entropy(hidden). < 1 means the learned profile is narrower than the truth.
    Undefined (None) when the hidden profile has a single subtopic (entropy 0)."""
    h = entropy(hidden)
    return entropy(learned) / h if h > 0 else None


def over_narrowing_by_user(snapshots: Mapping[str, Mapping[int, MemorySnapshot]],
                           profiles: Mapping[str, SimProfile]) -> dict[str, float | None]:
    """Over-narrowing at each user's last snapshot."""
    out: dict[str, float | None] = defaultdict(lambda: None)
    for u, snaps in snapshots.items():
        if not snaps:
            continue
        d = max(snaps)
        out[u] = over_narrowing(learned_weights(snaps[d]), profiles[u].weights_at(d))
    return dict(out)
