"""N4 features: `StoryFeatures` per story and the rules-only importance score.

Engagement is taken from observations at or before the run time only (never a later value).
With `hn_signal=False` (ablation A2) HN observations are already removed upstream, and the HN
features are forced to None and get zero weight here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from app.agents.newsroom.normalize import AGGREGATOR_HOSTS, NormItem, registrable_domain
from app.agents.newsroom.settings import RulesWeights
from app.config.loader import BeatCfg
from app.contracts.digest import StoryFeatures

FRONTPAGE_TYPES = {"techmeme", "hn_front", "hf_papers", "reddit_top", "frontpage"}
HN_TYPES = {"hn_front"}


class SourceIndex:
    """Lookups over a beat's headline sources."""

    def __init__(self, beat: BeatCfg, primary_source_ids: Sequence[str] = ()) -> None:
        self.cfg = {s.id: s for s in beat.headline_sources}
        self.primary_ids = set(primary_source_ids) | {
            s.id for s in beat.headline_sources if s.type == "arxiv"
        }
        self.primary_domains = {"arxiv.org"} | {
            registrable_domain(s.url) for s in beat.headline_sources if s.id in self.primary_ids and s.url
        }

    def tier(self, source_id: str) -> int | None:
        s = self.cfg.get(source_id)
        return s.tier if s else None

    def type(self, source_id: str) -> str | None:
        s = self.cfg.get(source_id)
        return s.type if s else None

    def is_hn(self, source_id: str) -> bool:
        t = self.type(source_id)
        return t in HN_TYPES if t else source_id.startswith("hn")

    def is_frontpage(self, source_id: str) -> bool:
        return self.type(source_id) in FRONTPAGE_TYPES


def compute_features(
    items: Sequence[NormItem],
    sources: SourceIndex,
    *,
    at: datetime,
    last_run_at: datetime,
    hn_signal: bool = True,
) -> StoryFeatures:
    obs = [o for it in items for o in it.observations if o.observed_at <= at]
    domains = {
        it.domain for it in items if not it.unresolved_aggregator and it.domain not in AGGREGATOR_HOSTS
    }
    tiers = [t for t in (sources.tier(o.source_id) for o in obs) if t is not None]
    fp = [o for o in obs if sources.is_frontpage(o.source_id) and o.rank is not None]
    hn_points = hn_comments = None
    if hn_signal:
        # latest observed value per (source); max across HN observations up to `at`
        pts = [o.engagement.hn_points for o in obs if o.engagement and o.engagement.hn_points is not None]
        cms = [
            o.engagement.comments
            for o in obs
            if o.engagement and o.engagement.comments is not None and sources.is_hn(o.source_id)
        ]
        hn_points = max(pts) if pts else None
        hn_comments = max(cms) if cms else None
    has_primary = any(o.source_id in sources.primary_ids for o in obs) or any(
        it.domain in sources.primary_domains for it in items
    )
    return StoryFeatures(
        n_items=len(items),
        n_independent_domains=len(domains),
        best_tier=min(tiers) if tiers else None,
        frontpage_hits=len({(o.source_id, o.observed_at) for o in fp}),
        best_rank=min(o.rank for o in fp) if fp else None,  # type: ignore[type-var]
        hn_points_at_observation=hn_points,
        comments_at_observation=hn_comments,
        first_seen=min(it.first_seen for it in items) if items else None,
        velocity=sum(1 for it in items if it.first_seen > last_run_at),
        has_primary_source=has_primary,
    )


_TIER_SCORE = {1: 1.0, 2: 0.6, 3: 0.3}


def rules_components(f: StoryFeatures) -> dict[str, float]:
    """Each feature normalised to [0, 1]."""
    return {
        "coverage": min(f.n_independent_domains, 8) / 8,
        "frontpage": min(f.frontpage_hits, 6) / 6,
        "tier": _TIER_SCORE.get(f.best_tier, 0.0) if f.best_tier is not None else 0.0,
        "rank": 1 / (1 + (f.best_rank - 1) / 5) if f.best_rank else 0.0,
        "hn_points": min(1.0, math.log1p(f.hn_points_at_observation or 0) / math.log1p(500)),
        "hn_comments": min(1.0, math.log1p(f.comments_at_observation or 0) / math.log1p(300)),
        "velocity": min(f.velocity, 5) / 5,
        "primary": 1.0 if f.has_primary_source else 0.0,
    }


def importance_rules(f: StoryFeatures, weights: RulesWeights, *, hn_signal: bool = True) -> float:
    """Rules-only importance on the same 0-100 scale as the LLM's importance.

    A weighted mean of normalised features; with hn_signal=False the HN terms are dropped and the
    remaining weights renormalised.
    """
    w = weights.model_dump()
    if not hn_signal:
        w["hn_points"] = w["hn_comments"] = 0.0
    total = sum(w.values())
    if total <= 0:
        return 0.0
    comp = rules_components(f)
    return round(100 * sum(w[k] * comp[k] for k in w) / total, 2)


def rules_evidence(f: StoryFeatures, source_ids: Sequence[str]) -> list[str]:
    """Evidence strings for a rules-only entry (feature references, never background knowledge)."""
    ev = [f"{f.n_independent_domains} independent domains", f"{f.n_items} items"]
    if f.best_rank is not None:
        ev.append(f"best front-page rank {f.best_rank}")
    if f.frontpage_hits:
        ev.append(f"frontpage_hits {f.frontpage_hits}")
    if f.best_tier is not None:
        ev.append(f"best source tier {f.best_tier}")
    if f.hn_points_at_observation is not None:
        ev.append(f"hn {f.hn_points_at_observation} points at observation")
    if f.has_primary_source:
        ev.append("primary source present")
    ev.append("sources: " + ", ".join(sorted(source_ids)))
    return ev
