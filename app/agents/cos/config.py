"""Typed views of the chief-of-staff sections of config/agent.yaml, with experiment overrides."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.config import loader
from app.config.loader import ExperimentCfg, ExperimentFlags


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SlotCounts(_Cfg):
    big_news: int = 2
    for_you: int = 3
    outside_usual: int = 1


class SelectionConfig(_Cfg):
    k_default: int = 6
    k_min: int = 3
    k_max: int = 8
    slots: SlotCounts = SlotCounts()
    mmr_lambda: float = 0.7
    min_importance_outside_usual: int = 50
    carryover_max: int = 1
    update_requires_new_facts: bool = True
    min_beat_share: dict[str, float] = {}


class PackDepthCfg(_Cfg):
    docs: int
    tokens: int


class PacksConfig(_Cfg):
    deep: PackDepthCfg = PackDepthCfg(docs=3, tokens=2500)
    standard: PackDepthCfg = PackDepthCfg(docs=2, tokens=1500)
    light: PackDepthCfg = PackDepthCfg(docs=1, tokens=800)
    deep_threshold: float = 0.6
    light_threshold: float = 0.25


class CosBudgetCfg(_Cfg):
    input_tokens: int = 90000
    searches: int = 12
    fetch_per_story: int = 3


class FollowupBudgetCfg(_Cfg):
    input_tokens: int = 50000
    searches: int = 8
    fetches: int = 6


class CosConfig(_Cfg):
    arm: str = "full"
    flags: ExperimentFlags = ExperimentFlags()
    selection: SelectionConfig = SelectionConfig()
    packs: PacksConfig = PacksConfig()
    budget: CosBudgetCfg = CosBudgetCfg()
    followup_budget: FollowupBudgetCfg = FollowupBudgetCfg()
    digest_top_followed: int = 30
    digest_top_other: int = 10
    pool_max: int = 80
    score_batch_size: int = 20
    search_lookback_days: int = 2
    max_refine_rounds: int = 4
    write_reserve: float = 0.25  # share of the input-token budget held back for C7 + C8


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def resolve_experiment(
    exp: ExperimentCfg | ExperimentFlags | str | None,
) -> tuple[str, ExperimentFlags, dict]:
    if exp is None:
        exp = "full"
    if isinstance(exp, str):
        exp = loader.experiment(exp)
    if isinstance(exp, ExperimentFlags):
        return "custom", exp, {}
    return exp.id, exp.flags, dict(exp.selection_overrides)


def cos_config(
    exp: ExperimentCfg | ExperimentFlags | str | None = None, agent_cfg: dict[str, Any] | None = None
) -> CosConfig:
    """Build the chief-of-staff config: agent.yaml defaults + the experiment's flags and overrides."""
    arm, flags, overrides = resolve_experiment(exp)
    agent_cfg = agent_cfg if agent_cfg is not None else loader.agent()
    selection = _merge(agent_cfg.get("selection", {}), overrides)
    budgets = agent_cfg.get("budgets", {})
    return CosConfig(
        arm=arm,
        flags=flags,
        selection=SelectionConfig(**selection),
        packs=PacksConfig(**agent_cfg.get("packs", {})),
        budget=CosBudgetCfg(**budgets.get("cos_daily", {})),
        followup_budget=FollowupBudgetCfg(**budgets.get("followup", {})),
    )
