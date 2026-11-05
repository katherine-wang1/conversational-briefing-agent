"""Simulated-user profile schema (TDD §17.1) and profile-at-day helpers.

Profiles are hand-authored YAML files under `eval/sim/profiles/<panel>/`. They are hidden from the
agent: only the simulator (and the graders) read them. The agent sees nothing but the `onboarding`
text, which the runner uses to create the synthetic account.

Day indexing: `day` in drift events is a 0-based offset from the first day of the run's window.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import loader

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

Role = Literal["researcher", "builder", "pm", "investor", "policy_safety", "journalist", "student", "generalist"]
Split = Literal["test", "dev", "scenario", "kate"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HiddenInterest(_M):
    subtopic: str
    weight: float = Field(gt=0)
    entities: list[str] = []
    keywords: list[str] = []


class LatentInterest(_M):
    """Never stated by the simulator; used only to decide engagement (adjacent-discovery target)."""

    subtopic: str | None = None
    entities: list[str] = []
    keywords: list[str] = []
    weight: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _has_anchor(self) -> LatentInterest:
        if not (self.subtopic or self.entities or self.keywords):
            raise ValueError("latent interest needs a subtopic, entities or keywords")
        return self

    @property
    def terms(self) -> list[str]:
        return [*self.entities, *self.keywords]


class Delivery(_M):
    length_s: int = Field(ge=30, le=1200)
    depth: Literal["headline", "standard", "deep"]
    technicality: Literal["plain", "medium", "high"]
    style: Literal["numbers", "narrative", "balanced"]


class DriftEvent(_M):
    """`add`: new hidden interest. `fade`: linear fade of a subtopic to 0 over `over_days`.
    `remove`: instant removal. `shift`: replace all hidden interests at once (sudden drift)."""

    day: int = Field(ge=0)
    op: Literal["add", "fade", "remove", "shift"]
    interest: HiddenInterest | None = None
    subtopic: str | None = None
    interests: list[HiddenInterest] = []
    over_days: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _shape(self) -> DriftEvent:
        if self.op == "add" and self.interest is None:
            raise ValueError("add needs `interest`")
        if self.op in ("fade", "remove") and not self.subtopic:
            raise ValueError(f"{self.op} needs `subtopic`")
        if self.op == "shift" and not self.interests:
            raise ValueError("shift needs `interests`")
        return self


class TimeBudget(_M):
    dist: Literal["mixture", "uniform", "fixed"] = "mixture"
    rushed_p: float = Field(default=0.0, ge=0, le=1)
    rushed: tuple[int, int] = (60, 150)
    relaxed: tuple[int, int] = (300, 600)
    fixed_s: int | None = None


class Behavior(_M):
    explicitness: float = Field(ge=0, le=1)
    followup_rate: float = Field(ge=0, le=1)
    time_budget_s: TimeBudget
    exploration_openness: float = Field(ge=0, le=1)
    noise: float = Field(ge=0, le=1)
    max_questions: int = Field(default=3, ge=0, le=3)
    unanswerable_rate: float = Field(default=0.15, ge=0, le=1)
    speaking_wps: float = Field(default=2.5, gt=0)  # words per second for brief / answer audio
    think_s: float = Field(default=2.0, ge=0)  # pause before speaking


class SimProfile(_M):
    id: str
    split: Split
    role: Role
    persona: str
    onboarding: str  # what the user tells the agent at sign-up (partial, may be wrong for noisy users)
    hidden_interests: list[HiddenInterest]
    latent_interests: list[LatentInterest] = []
    delivery: Delivery
    drift: list[DriftEvent] = []
    behavior: Behavior
    brief_time: str = "07:00"  # local (UTC in replay)
    calibrated_to: str | None = None
    placeholder: bool = False  # True until filled from real data (sim_kate)
    authored: str = "hand-authored test content (W-G); not collected data"
    notes: str = ""

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> SimProfile:
        s = sum(i.weight for i in self.hidden_interests)
        if not math.isclose(s, 1.0, abs_tol=1e-6):
            raise ValueError(f"{self.id}: hidden interest weights sum to {s}, not 1")
        subs = [i.subtopic for i in self.hidden_interests]
        if len(subs) != len(set(subs)):
            raise ValueError(f"{self.id}: duplicate hidden subtopics")
        for ev in self.drift:
            if ev.op == "shift" and not math.isclose(sum(i.weight for i in ev.interests), 1.0, abs_tol=1e-6):
                raise ValueError(f"{self.id}: shift interests must sum to 1")
        return self

    # ---- profile at a given day ----------------------------------------------------------------

    def interests_at(self, day: int) -> list[HiddenInterest]:
        """Hidden interests at window day `day` after applying drift; weights renormalised to 1."""
        cur: dict[str, HiddenInterest] = {i.subtopic: i.model_copy() for i in self.hidden_interests}
        base_w: dict[str, float] = {k: v.weight for k, v in cur.items()}
        for ev in sorted(self.drift, key=lambda e: e.day):
            if ev.day > day:
                break
            if ev.op == "add":
                assert ev.interest is not None
                cur[ev.interest.subtopic] = ev.interest.model_copy()
                base_w[ev.interest.subtopic] = ev.interest.weight
            elif ev.op == "remove":
                cur.pop(ev.subtopic or "", None)
            elif ev.op == "fade":
                key = ev.subtopic or ""
                if key in cur:
                    frac = min(1.0, (day - ev.day + 1) / ev.over_days)
                    w = base_w[key] * (1 - frac)
                    if w <= 1e-9:
                        cur.pop(key)
                    else:
                        cur[key] = cur[key].model_copy(update={"weight": w})
            elif ev.op == "shift":
                cur = {i.subtopic: i.model_copy() for i in ev.interests}
                base_w = {k: v.weight for k, v in cur.items()}
        total = sum(i.weight for i in cur.values())
        return [i.model_copy(update={"weight": i.weight / total}) for i in cur.values()]

    def weights_at(self, day: int) -> dict[str, float]:
        return {i.subtopic: i.weight for i in self.interests_at(day)}

    def drift_targets(self) -> list[tuple[int, str, float]]:
        """(day, subtopic, target normalised weight) for each add/fade/remove/shift event.

        For fades/removals the target is 0. Used by the drift-lag grader.
        """
        out: list[tuple[int, str, float]] = []
        for ev in self.drift:
            settle = ev.day + (ev.over_days - 1 if ev.op == "fade" else 0)
            w = self.weights_at(settle)
            if ev.op == "add":
                assert ev.interest is not None
                out.append((ev.day, ev.interest.subtopic, w.get(ev.interest.subtopic, 0.0)))
            elif ev.op in ("fade", "remove"):
                out.append((ev.day, ev.subtopic or "", 0.0))
            elif ev.op == "shift":
                prev = self.weights_at(ev.day - 1) if ev.day > 0 else {}
                for k in set(prev) | set(w):
                    out.append((ev.day, k, w.get(k, 0.0)))
        return out


def ai_subtopic_ids() -> set[str]:
    return {s.id for s in loader.taxonomy().subtopics if s.beat == "ai"}


def validate_taxonomy(p: SimProfile) -> list[str]:
    """Hidden and latent interests must use AI-beat subtopic ids (PRD §9: core results on the AI beat)."""
    allowed = ai_subtopic_ids()
    errs = []
    subs = [i.subtopic for i in p.hidden_interests] + [i.subtopic for i in p.latent_interests if i.subtopic]
    for ev in p.drift:
        if ev.interest:
            subs.append(ev.interest.subtopic)
        if ev.subtopic:
            subs.append(ev.subtopic)
        subs += [i.subtopic for i in ev.interests]
    for s in subs:
        if s not in allowed:
            errs.append(f"{p.id}: subtopic {s!r} is not an AI-beat taxonomy id")
    return errs


def load_profile(path: Path) -> SimProfile:
    with path.open() as f:
        return SimProfile(**yaml.safe_load(f))


@lru_cache
def load_panel(panel: str = "panel_v1") -> tuple[SimProfile, ...]:
    paths = sorted((PROFILES_DIR / panel).glob("*.yaml"))
    profiles = tuple(load_profile(p) for p in paths)
    ids = [p.id for p in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate profile ids in {panel}")
    return profiles


def panel_split(split: Split | Literal["all"], panel: str = "panel_v1") -> list[SimProfile]:
    return [p for p in load_panel(panel) if split == "all" or p.split == split]
