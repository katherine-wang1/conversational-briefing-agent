"""Small, dependency-free numeric helpers used by the graders and statistics."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence


def normalise(w: Mapping[str, float]) -> dict[str, float]:
    total = sum(v for v in w.values() if v > 0)
    if total <= 0:
        return {k: 0.0 for k in w}
    return {k: max(v, 0.0) / total for k, v in w.items()}


def entropy(p: Mapping[str, float] | Iterable[float], base: float = 2.0) -> float:
    """Shannon entropy (bits by default) of a non-negative weight vector; normalised first."""
    vals = list(p.values()) if isinstance(p, Mapping) else list(p)
    total = sum(v for v in vals if v > 0)
    if total <= 0:
        return 0.0
    return -sum((v / total) * math.log(v / total, base) for v in vals if v > 0)


def rankdata(x: Sequence[float]) -> list[float]:
    """Average ranks (1-based), ties get the mean of their positions (like scipy.stats.rankdata)."""
    order = sorted(range(len(x)), key=lambda i: x[i])
    ranks = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(a: Sequence[float], b: Sequence[float]) -> float | None:
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (sa * sb)


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Spearman's rho with average ranks for ties. None if either side is constant."""
    return pearson(rankdata(a), rankdata(b))


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (numpy's default method), q in [0, 100]."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def mean(values: Iterable[float]) -> float | None:
    v = list(values)
    return sum(v) / len(v) if v else None
