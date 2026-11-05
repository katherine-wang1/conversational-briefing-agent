"""Typed loaders for config/*.yaml. Every workstream reads config through here."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Subtopic(_M):
    id: str
    beat: str
    description: str
    examples: list[str] = []


class Taxonomy(_M):
    version: int
    subtopics: list[Subtopic]

    @property
    def ids(self) -> set[str]:
        return {s.id for s in self.subtopics}


class HeadlineSourceCfg(_M):
    id: str
    type: Literal["techmeme", "hn_front", "hf_papers", "reddit_top", "arxiv", "rss", "frontpage"]
    tier: int
    weight: float
    url: str | None = None
    params: dict[str, Any] = {}
    verified: bool = False


class BeatCfg(_M):
    id: str
    name: str
    scope: str
    boundaries: list[str] = []
    out_of_scope: list[str] = []
    subtopics: list[str]
    digest_size: int
    refresh_live_utc: list[str]
    headline_sources: list[HeadlineSourceCfg]


class ModelRole(_M):
    provider: Literal["openrouter", "tinker"]
    model: str
    params: dict[str, Any] = {}  # extra request-body fields (e.g. OpenRouter `reasoning`, `provider`)


class ModelsCfg(_M):
    dev_default: ModelRole
    roles: dict[str, ModelRole]

    def for_role(self, role: str) -> ModelRole:
        r = self.roles.get(role)
        if r is None or r.model.startswith("TBD"):
            return self.dev_default
        return r


class ExperimentFlags(_M):
    personalization: bool = True
    memory: Literal["learn", "static"] = "learn"
    pipeline: Literal["standard", "naive"] = "standard"
    prefetch: bool = True
    newsroom_separation: bool = True
    hn_signal: bool = True
    session_ctx_weighting: bool = True
    claim_check: bool = True


class ExperimentCfg(_M):
    id: str
    description: str
    flags: ExperimentFlags
    selection_overrides: dict[str, Any] = {}


def _read(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


@lru_cache
def taxonomy() -> Taxonomy:
    return Taxonomy(**_read(CONFIG_DIR / "taxonomy.yaml"))


@lru_cache
def beats() -> dict[str, BeatCfg]:
    out = {}
    for p in sorted((CONFIG_DIR / "beats").glob("*.yaml")):
        b = BeatCfg(**_read(p))
        out[b.id] = b
    return out


@lru_cache
def models() -> ModelsCfg:
    return ModelsCfg(**_read(CONFIG_DIR / "models.yaml"))


@lru_cache
def agent() -> dict[str, Any]:
    """Agent parameters stay a plain dict; each component validates the section it uses."""
    return _read(CONFIG_DIR / "agent.yaml")


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@lru_cache
def experiment(exp_id: str) -> ExperimentCfg:
    raw = _read(CONFIG_DIR / "experiments" / f"{exp_id}.yaml")
    parent = raw.pop("extends", None)
    if parent:
        base = experiment(parent).model_dump()
        merged = _deep_merge(base, {k: v for k, v in raw.items() if k != "id"})
        merged["id"] = raw["id"]
        return ExperimentCfg(**merged)
    return ExperimentCfg(**raw)
