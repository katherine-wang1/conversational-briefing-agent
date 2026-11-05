"""Typed view of the config sections the newsroom uses (config/agent.yaml)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.config import loader


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClusteringSettings(_M):
    tau: float = 0.78
    tiebreak_band: float = 0.08
    identity_lookback_days: int = 7


class EmbedderSettings(_M):
    name: str = "fastembed"
    model: str = "BAAI/bge-small-en-v1.5"


class RulesWeights(_M):
    coverage: float = 0.30
    frontpage: float = 0.20
    tier: float = 0.15
    rank: float = 0.10
    hn_points: float = 0.10
    hn_comments: float = 0.05
    velocity: float = 0.05
    primary: float = 0.05


class NewsroomSettings(_M):
    window_hours: float = 24
    update_gap_hours: float = 12
    score_candidates: int = 80
    score_batch_size: int = 20
    score_max_output_tokens: int = 6000
    score_floor: int = 20
    tiebreak_batch_size: int = 20
    tiebreak_max_pairs: int = 40
    tiebreak_max_output_tokens: int = 1500
    fetch_max_tokens: int = 800
    embedder: EmbedderSettings = EmbedderSettings()
    primary_sources: dict[str, list[str]] = {}
    rules_weights: RulesWeights = RulesWeights()


class NewsroomBudget(_M):
    input_tokens: int = 80000
    fetches: int = 5


class Settings(_M):
    """Everything the newsroom reads from agent.yaml, in one object (hashed into Digest.version)."""

    newsroom: NewsroomSettings = NewsroomSettings()
    clustering: ClusteringSettings = ClusteringSettings()
    budget: NewsroomBudget = NewsroomBudget()

    @classmethod
    def from_config(cls, overrides: dict[str, Any] | None = None) -> Settings:
        cfg = loader.agent()
        raw: dict[str, Any] = {
            "newsroom": cfg.get("newsroom", {}),
            "clustering": cfg.get("clustering", {}),
            "budget": cfg.get("budgets", {}).get("newsroom_run", {}),
        }
        if overrides:
            raw = loader._deep_merge(raw, overrides)
        return cls(**raw)
