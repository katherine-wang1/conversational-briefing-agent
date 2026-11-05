"""Typed view of the `memory` section of config/agent.yaml (TDD §9.3, Appendix B)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.config import loader


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SkipRule(_Cfg):
    min_sessions: int = 2
    window: int = 4


class BetaPrior(_Cfg):
    a: float = 1
    b: float = 2


class OnboardingDefaults(_Cfg):
    weight: float = 0.4
    confidence: float = 0.3
    delivery_confidence: float = 0.5


class MemoryConfig(_Cfg):
    eta_explicit: float = 0.30
    eta_implicit: float = 0.15
    implicit_strength: dict[str, float] = {"dive": 0.5, "followup": 0.3, "save_for_later": 0.4, "skip": 0.2}
    half_life_implicit_d: float = 21
    half_life_explicit_d: float = 60
    max_subtopic_share: float = 0.35
    cut_short_opportunity: float = 0.3
    skip_rule: SkipRule = SkipRule()
    blend_implicit: float = 0.3
    beta_prior: BetaPrior = BetaPrior()
    floor: float = 0.05
    floor_after_days: int = 14
    onboarding: OnboardingDefaults = OnboardingDefaults()
    # Project choices not fixed by the TDD (documented in rules.py):
    explicit_confidence: float = 0.8
    implicit_confidence_step: float = 0.05
    implicit_confidence_cap: float = 0.8
    new_implicit_confidence: float = 0.2
    implicit_delivery_confidence: float = 0.4
    evidence_max: int = 20
    budget_input_tokens: int = 20000


def memory_config(agent_cfg: dict[str, Any] | None = None) -> MemoryConfig:
    agent_cfg = agent_cfg if agent_cfg is not None else loader.agent()
    raw = dict(agent_cfg.get("memory", {}))
    budget = agent_cfg.get("budgets", {}).get("memory_reflect", {})
    if "input_tokens" in budget:
        raw["budget_input_tokens"] = budget["input_tokens"]
    return MemoryConfig(**raw)
