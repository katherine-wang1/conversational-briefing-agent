"""Run manifest (TDD §16.1) and computation-reuse flags (§16.2)."""

from __future__ import annotations

import glob
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import loader
from eval.sim.profile import SimProfile, load_profile

REPO_ROOT = Path(__file__).resolve().parents[2]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(_M):
    start: date
    days: int = Field(ge=1)
    slice: Literal["test", "dev"] = "test"

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(self.days)]


class Reuse(_M):
    """Computation reuse (TDD §16.2, 02a §8).

    - `digests_from`: shared newsroom outputs per day (run id of the digests run, e.g. digests-v1).
    - `reuse_scores_from`: B3 reuses Full's candidates and C4 scores; only C5 onward re-runs.
    - `briefs_from`: B4 reuses Full's briefs (same cos_daily outputs); only sessions differ.
    - `day_wise_counterfactual_from`: evaluate on another run's memory trajectory: each day starts
      from that run's previous-day snapshot (B3/B4, [OQ-11b]). Report as a day-wise counterfactual.
    """

    digests_from: str | None = None
    reuse_scores_from: str | None = None
    briefs_from: str | None = None
    day_wise_counterfactual_from: str | None = None


class RunManifest(_M):
    run_id: str
    experiment: str
    seed: int
    window: Window
    panel: str = "eval/sim/profiles/panel_v1/*.yaml"
    panel_split: Literal["test", "dev", "scenario", "kate", "all"] | None = None  # default: window.slice
    users: list[str] | None = None  # optional subset of profile ids
    models: str = "config/models.yaml"
    cache_namespace: str | None = None  # default: run_id (must be unique per seed)
    beats: list[str] = ["ai"]
    concurrency: int = Field(default=6, ge=1)
    newsroom_hour_utc: int = 6
    cos_lead_min: int = 60
    followup_delay_h: int = 2
    reuse: Reuse = Reuse()

    @model_validator(mode="after")
    def _check(self) -> RunManifest:
        if not (loader.CONFIG_DIR / "experiments" / f"{self.experiment}.yaml").exists():
            raise ValueError(f"unknown experiment {self.experiment!r}")
        loader.experiment(self.experiment)
        if self.reuse.briefs_from and self.reuse.reuse_scores_from:
            raise ValueError("briefs_from and reuse_scores_from are mutually exclusive")
        unknown = set(self.beats) - set(loader.beats())
        if unknown:
            raise ValueError(f"unknown beats {unknown}")
        return self

    @property
    def namespace(self) -> str:
        return self.cache_namespace or self.run_id

    @property
    def digests_run_id(self) -> str:
        return self.reuse.digests_from or f"{self.run_id}-digests"

    def profiles(self) -> list[SimProfile]:
        split = self.panel_split or self.window.slice
        pattern = self.panel if Path(self.panel).is_absolute() else str(REPO_ROOT / self.panel)
        out = [load_profile(Path(p)) for p in sorted(glob.glob(pattern))]
        out = [p for p in out if split == "all" or p.split == split]
        if self.users is not None:
            missing = set(self.users) - {p.id for p in out}
            if missing:
                raise ValueError(f"users not in panel/split: {sorted(missing)}")
            out = [p for p in out if p.id in self.users]
        if not out:
            raise ValueError("manifest selects no simulated users")
        return out


def load_manifest(path: Path) -> RunManifest:
    with path.open() as f:
        return RunManifest(**yaml.safe_load(f))


def check_namespaces(manifests: Iterable[RunManifest]) -> list[str]:
    """One cache namespace per seed: two manifests may share a namespace only if they have the same
    seed *and* experiment (i.e. they are re-runs). Returns problems."""
    seen: dict[str, tuple[int, str, str]] = {}
    problems = []
    for m in manifests:
        key = (m.seed, m.experiment, m.run_id)
        prev = seen.setdefault(m.namespace, key)
        if prev[:2] != key[:2]:
            problems.append(
                f"cache namespace {m.namespace!r} shared by {prev[2]} and {m.run_id} (seed/experiment differ)"
            )
    return problems
