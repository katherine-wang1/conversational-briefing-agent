from datetime import UTC, datetime, timedelta

import pytest

from app.agents.newsroom.features import SourceIndex, compute_features, importance_rules
from app.agents.newsroom.normalize import normalize
from app.agents.newsroom.prompts import load_prompt
from app.agents.newsroom.settings import RulesWeights, Settings
from app.config import loader
from app.contracts.digest import StoryFeatures
from app.contracts.sources import Engagement, HeadlineObservation

AT = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
BEAT = loader.beats()["ai"]
SOURCES = SourceIndex(BEAT, ["openai_news", "hf_blog"])


def ob(source, url, minutes_before, rank=None, pts=None, com=None):
    eng = Engagement(hn_points=pts, comments=com) if pts is not None else None
    return HeadlineObservation(
        source_id=source,
        beat="ai",
        observed_at=AT - timedelta(minutes=minutes_before),
        url=url,
        title="Acme ships X-1",
        rank=rank,
        engagement=eng,
    )


def test_features_from_observations():
    items = normalize(
        [
            ob("techmeme", "https://a.example/x", 300, rank=3),
            ob("techmeme", "https://a.example/x", 60, rank=1),
            ob("hn_front", "https://b.example/y", 200, rank=5, pts=100, com=20),
            ob("hn_front", "https://b.example/y", 30, rank=2, pts=250, com=80),
            ob("ars_ai", "https://news.c.example/z", 1500, rank=1),  # rss: not a front page
            ob("arxiv_cs_cl", "https://arxiv.org/abs/2610.99999", 100, rank=40),
        ],
        {s.id: s.tier for s in BEAT.headline_sources},
    )
    f = compute_features(items, SOURCES, at=AT, last_run_at=AT - timedelta(hours=24))
    assert f.n_items == 4
    assert f.n_independent_domains == 4
    assert f.best_tier == 1
    assert f.frontpage_hits == 4
    assert f.best_rank == 1
    assert f.hn_points_at_observation == 250 and f.comments_at_observation == 80
    assert f.has_primary_source  # arXiv counts as a primary source
    assert f.velocity == 3  # the ars item was first seen > 24 h before the run
    assert f.first_seen == AT - timedelta(minutes=1500)
    f2 = compute_features(items, SOURCES, at=AT, last_run_at=AT, hn_signal=False)
    assert f2.hn_points_at_observation is None and f2.velocity == 0


def test_primary_source_by_domain_of_configured_feed():
    items = normalize([ob("techmeme", "https://openai.com/index/some-post", 10, rank=2)])
    assert compute_features(items, SOURCES, at=AT, last_run_at=AT).has_primary_source


def test_importance_rules_scale_and_monotonic():
    w = RulesWeights()
    low = StoryFeatures(n_items=1, n_independent_domains=1, best_tier=3)
    high = StoryFeatures(
        n_items=6,
        n_independent_domains=6,
        best_tier=1,
        frontpage_hits=5,
        best_rank=1,
        hn_points_at_observation=400,
        comments_at_observation=200,
        velocity=4,
        has_primary_source=True,
    )
    lo, hi = importance_rules(low, w), importance_rules(high, w)
    assert 0 <= lo < hi <= 100
    assert importance_rules(StoryFeatures(), w) == 0
    maxed = StoryFeatures(
        n_independent_domains=9,
        best_tier=1,
        frontpage_hits=9,
        best_rank=1,
        hn_points_at_observation=10**4,
        comments_at_observation=10**4,
        velocity=9,
        has_primary_source=True,
    )
    assert importance_rules(maxed, w) == pytest.approx(100)


def test_importance_rules_ignores_hn_under_a2():
    w = RulesWeights()
    base = StoryFeatures(n_independent_domains=3, best_tier=1)
    with_hn = base.model_copy(update={"hn_points_at_observation": 500, "comments_at_observation": 300})
    assert importance_rules(with_hn, w, hn_signal=False) == importance_rules(base, w, hn_signal=False)
    assert importance_rules(with_hn, w) > importance_rules(base, w)


def test_settings_load_from_agent_yaml():
    s = Settings.from_config()
    assert s.clustering.tau == 0.78 and s.clustering.identity_lookback_days == 7
    assert s.budget.fetches == 5 and s.budget.input_tokens == 80000
    assert "hf_blog" in s.newsroom.primary_sources["ai"]
    assert sum(s.newsroom.rules_weights.model_dump().values()) == pytest.approx(1.0)


def test_prompts_have_front_matter_matching_roles():
    for pid in ("newsroom.score", "newsroom.cluster_tiebreak"):
        p = load_prompt(pid)
        assert p.role == pid and p.version and len(p.sha256) == 64
    with pytest.raises(KeyError):
        load_prompt("newsroom.score").render(beat_name="x")
