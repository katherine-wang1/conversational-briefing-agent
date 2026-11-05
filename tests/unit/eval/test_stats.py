"""Statistics: cluster bootstrap, paired comparisons, per-dimension breakdowns, seed variance."""

from __future__ import annotations

import pytest

from eval.analysis import stats
from eval.sim.coverage import categorize
from eval.sim.profile import panel_split


def test_cluster_bootstrap_basic_properties():
    data = {"u1": [1, 1, 1], "u2": [0, 0], "u3": [1, 0]}
    ci = stats.cluster_bootstrap(data, n_resamples=2000, seed=1)
    assert ci.estimate == pytest.approx(4 / 7)
    assert 0 <= ci.lo <= ci.estimate <= ci.hi <= 1
    assert (ci.n_clusters, ci.n_units) == (3, 7)
    um = stats.cluster_bootstrap(data, n_resamples=200, estimator="user_mean", seed=1)
    assert um.estimate == pytest.approx((1 + 0 + 0.5) / 3)
    # reproducible from the seed
    assert stats.cluster_bootstrap(data, n_resamples=500, seed=3) == stats.cluster_bootstrap(
        data, n_resamples=500, seed=3
    )
    # constant data -> zero-width interval
    c = stats.cluster_bootstrap({"a": [2.0], "b": [2.0]}, n_resamples=100)
    assert c.lo == c.hi == 2.0
    assert stats.cluster_bootstrap({}).estimate is None


def test_cluster_bootstrap_is_wider_than_naive_when_users_differ():
    # Two very different users: clustering by user must give a wide interval.
    data = {"a": [1.0] * 50, "b": [0.0] * 50}
    ci = stats.cluster_bootstrap(data, n_resamples=2000, seed=0)
    assert ci.hi - ci.lo > 0.5


def test_paired_comparison_detects_consistent_improvement():
    a = {(u, d): 0.6 + 0.01 * d for u in ("u1", "u2", "u3", "u4", "u5", "u6") for d in range(5)}
    b = {k: v - 0.1 for k, v in a.items()}
    b[("u9", 0)] = 0.0  # unmatched key is ignored
    r = stats.paired_comparison(a, b, n_resamples=2000, n_permutations=2000)
    assert r.diff.estimate == pytest.approx(0.1)
    assert r.diff.lo == pytest.approx(0.1) and r.n_pairs == 30 and r.unmatched_b == 1
    assert r.p_value < 0.05


def test_paired_comparison_null_has_large_p():
    a = {("u1", 0): 1.0, ("u2", 0): 0.0, ("u3", 0): 1.0, ("u4", 0): 0.0}
    b = {("u1", 0): 0.0, ("u2", 0): 1.0, ("u3", 0): 0.0, ("u4", 0): 1.0}
    r = stats.paired_comparison(a, b, n_resamples=500, n_permutations=2000)
    assert r.diff.estimate == 0 and r.p_value > 0.5


def test_per_dimension_uses_panel_categories():
    dims = {p.id: categorize(p) for p in panel_split("test")}
    vals = {p.id: [1.0 if categorize(p)["time"] == "rushed" else 0.0] for p in panel_split("test")}
    out = stats.per_dimension(vals, dims, n_resamples=200)
    assert out["time"]["rushed"].estimate == 1.0 and out["time"]["relaxed"].estimate == 0.0
    assert set(out["breadth"]) == {"specialist", "medium", "generalist"}


def test_seed_variance_and_helpers():
    s = stats.seed_variance({1: 0.5, 2: 0.7, 3: 0.6})
    assert s.mean == pytest.approx(0.6) and s.sd == pytest.approx(0.1)
    assert stats.seed_variance({1: 0.5}).sd is None
    g = stats.by_user(
        [{"user": "a", "v": 1}, {"user": "a", "v": None}, {"user": "b", "v": 0}], lambda r: r["v"]
    )
    assert g == {"a": [1.0], "b": [0.0]}
    row = stats.ci_row("full", stats.cluster_bootstrap(g, n_resamples=50))
    assert row.startswith("| full | 0.500 |")
