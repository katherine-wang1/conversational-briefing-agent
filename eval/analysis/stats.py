"""Statistics (TDD §18.4, PRD §10.7): cluster bootstrap over users, paired comparisons across
configs on the same (user, day), per-dimension breakdowns, and seed variance.

Pure Python (random.Random) so results are reproducible from a seed and need no numpy.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from eval.graders.mathutil import percentile

Estimator = Literal["pooled", "user_mean"]


class CI(BaseModel):
    estimate: float | None
    lo: float | None
    hi: float | None
    n_clusters: int
    n_units: int
    n_resamples: int


def _estimate(clusters: Sequence[Sequence[float]], how: Estimator) -> float | None:
    if how == "pooled":
        vals = [v for c in clusters for v in c]
        return sum(vals) / len(vals) if vals else None
    means = [sum(c) / len(c) for c in clusters if c]
    return sum(means) / len(means) if means else None


def cluster_bootstrap(
    values_by_user: Mapping[str, Sequence[float]],
    *,
    n_resamples: int = 10_000,
    alpha: float = 0.05,
    estimator: Estimator = "pooled",
    seed: int = 0,
) -> CI:
    """Resample users (clusters) with replacement; percentile CI of the estimator.

    `pooled` = mean over all units (user-days, items) of the resampled users; `user_mean` = mean of
    per-user means (each user weighs the same)."""
    clusters = [list(v) for v in values_by_user.values() if len(v)]
    est = _estimate(clusters, estimator)
    if not clusters:
        return CI(estimate=None, lo=None, hi=None, n_clusters=0, n_units=0, n_resamples=0)
    rng = random.Random(seed)
    k = len(clusters)
    stats = []
    for _ in range(n_resamples):
        s = _estimate([clusters[rng.randrange(k)] for _ in range(k)], estimator)
        if s is not None:
            stats.append(s)
    return CI(
        estimate=est,
        lo=percentile(stats, 100 * alpha / 2),
        hi=percentile(stats, 100 * (1 - alpha / 2)),
        n_clusters=k,
        n_units=sum(len(c) for c in clusters),
        n_resamples=n_resamples,
    )


class PairedResult(BaseModel):
    diff: CI  # mean of (a - b) over matched units
    mean_a: float | None
    mean_b: float | None
    n_pairs: int
    unmatched_a: int
    unmatched_b: int
    p_value: float | None  # two-sided cluster sign-flip permutation test


def paired_comparison(
    a: Mapping[tuple[str, Hashable], float],
    b: Mapping[tuple[str, Hashable], float],
    *,
    n_resamples: int = 10_000,
    n_permutations: int = 10_000,
    estimator: Estimator = "pooled",
    seed: int = 0,
) -> PairedResult:
    """Paired comparison of two configs on the same units, keyed (user, unit) e.g. (user, day).
    Only keys present in both are used; differences are clustered by user for the bootstrap, and a
    user-level sign-flip permutation test gives a two-sided p-value."""
    keys = sorted(set(a) & set(b), key=str)
    diffs: dict[str, list[float]] = defaultdict(list)
    for k in keys:
        diffs[k[0]].append(a[k] - b[k])
    ci = cluster_bootstrap(diffs, n_resamples=n_resamples, estimator=estimator, seed=seed)
    p = _sign_flip_p(diffs, n_permutations, estimator, seed) if diffs else None
    ma = sum(a[k] for k in keys) / len(keys) if keys else None
    mb = sum(b[k] for k in keys) / len(keys) if keys else None
    return PairedResult(
        diff=ci,
        mean_a=ma,
        mean_b=mb,
        n_pairs=len(keys),
        unmatched_a=len(set(a) - set(b)),
        unmatched_b=len(set(b) - set(a)),
        p_value=p,
    )


def _sign_flip_p(diffs: Mapping[str, Sequence[float]], n: int, how: Estimator, seed: int) -> float:
    clusters = [list(v) for v in diffs.values()]
    obs = abs(_estimate(clusters, how) or 0.0)
    rng = random.Random(seed + 1)
    hits = 0
    for _ in range(n):
        flipped = [[-x for x in c] if rng.random() < 0.5 else c for c in clusters]
        if abs(_estimate(flipped, how) or 0.0) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n + 1)


def per_dimension(
    values_by_user: Mapping[str, Sequence[float]],
    user_dims: Mapping[str, Mapping[str, str]],
    *,
    n_resamples: int = 10_000,
    seed: int = 0,
) -> dict[str, dict[str, CI]]:
    """Breakdown by simulated-user dimension (e.g. time: rushed vs relaxed; breadth: specialist vs
    generalist). `user_dims` = profile id -> {dimension: category}, e.g. from eval.sim.coverage."""
    out: dict[str, dict[str, CI]] = defaultdict(dict)
    dims = {d for m in user_dims.values() for d in m}
    for dim in sorted(dims):
        groups: dict[str, dict[str, Sequence[float]]] = defaultdict(dict)
        for u, vals in values_by_user.items():
            if u in user_dims and dim in user_dims[u]:
                groups[user_dims[u][dim]][u] = vals
        for cat, g in sorted(groups.items()):
            out[dim][cat] = cluster_bootstrap(g, n_resamples=n_resamples, seed=seed)
    return dict(out)


class SeedSummary(BaseModel):
    seeds: list[int]
    values: list[float]
    mean: float
    sd: float | None


def seed_variance(by_seed: Mapping[int, float]) -> SeedSummary:
    vals = [by_seed[s] for s in sorted(by_seed)]
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else None
    return SeedSummary(seeds=sorted(by_seed), values=vals, mean=m, sd=sd)


def by_user(
    rows: Sequence[Mapping[str, object]],
    value: Callable[[Mapping[str, object]], float | None],
    user_key: str = "user",
) -> dict[str, list[float]]:
    """Group per-unit rows into {user: [values]} skipping None values."""
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = value(r)
        if v is not None:
            out[str(r[user_key])].append(float(v))
    return dict(out)


def ci_row(name: str, ci: CI, digits: int = 3) -> str:
    """Markdown table row for report/tables/*.md."""

    def f(x: float | None) -> str:
        return "-" if x is None else f"{x:.{digits}f}"

    return f"| {name} | {f(ci.estimate)} | [{f(ci.lo)}, {f(ci.hi)}] | {ci.n_clusters} | {ci.n_units} |"
