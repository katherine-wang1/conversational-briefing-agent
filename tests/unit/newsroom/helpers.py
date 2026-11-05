"""Shared setup for newsroom pipeline tests: the synthetic fixture and test settings."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.agents.newsroom.clustering import HashingEmbedder
from app.agents.newsroom.settings import Settings
from app.contracts.sources import HeadlineObservation

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "newsroom" / "synthetic_observations.json"


def load_fixture() -> tuple[list[HeadlineObservation], list[datetime]]:
    """SYNTHETIC TEST FIXTURE (invented stories); see make_synthetic_observations.py."""
    raw = json.loads(FIXTURE.read_text())
    assert raw["_note"].startswith("SYNTHETIC TEST FIXTURE")
    obs = [HeadlineObservation.model_validate(o) for o in raw["observations"]]
    return obs, [datetime.fromisoformat(r) for r in raw["runs"]]


def make_embedder() -> HashingEmbedder:
    # Offline stand-in for fastembed; its similarity scale needs a lower tau (see make_settings).
    return HashingEmbedder(name_weight=4)


def make_settings(**over) -> Settings:
    base = {"clustering": {"tau": 0.6, "tiebreak_band": 0.2}}
    for k, v in over.items():
        base.setdefault(k, {}).update(v)
    return Settings.from_config(base)
