"""Shared types and storage for the newsroom comparison."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

ROOT = Path(os.environ.get("NEWSROOM_COMPARE_DIR", "eval/data/newsroom_compare"))

# Source ids per beat as of 2026-11-26, before the editor arm widened the lists. The `classic` arm
# runs on these so it stays the product as it was; `classic_all` runs on every configured source.
ORIGINAL_SOURCES: dict[str, list[str]] = {
    "ai": [
        "techmeme", "hn_front", "hf_papers", "reddit_ml", "arxiv_cs_cl", "arxiv_cs_lg", "arxiv_cs_ai",
        "openai_news", "anthropic_news", "deepmind_blog", "meta_ai_blog", "hf_blog", "nvidia_blog",
        "techcrunch_ai", "verge_ai", "ars_ai", "mit_tr_ai", "import_ai",
    ],
    "energy": [
        "utility_dive", "canary_media", "latitude_media", "heatmap", "dcd", "dc_frontier",
        "electrek_energy", "carbon_brief", "pv_magazine", "energy_storage_news", "eia_today", "hn_front",
    ],
    "physical_ai": [
        "robot_report", "ieee_robotics", "robotics247", "techcrunch_robotics", "arxiv_cs_ro",
        "nvidia_blog", "hn_front", "reddit_robotics",
    ],
}

PAYWALLED = {
    "wsj.com", "ft.com", "bloomberg.com", "theinformation.com", "nytimes.com", "economist.com",
    "asia.nikkei.com", "washingtonpost.com", "barrons.com", "businessinsider.com",
}


class CompareStory(BaseModel):
    arm: str
    rank: int
    headline: str
    summary: str = ""
    urls: list[str] = []
    outlets: list[str] = []
    importance: float | None = None
    reason: str = ""


class ArmResult(BaseModel):
    arm: str
    beat: str
    at: datetime
    ok: bool = True
    error: str | None = None
    stories: list[CompareStory] = []
    wall_s: float | None = None
    peak_rss_mb: float | None = None
    cost_usd: float | None = None
    searches: int | None = None
    notes: dict = {}


def host(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h.removeprefix("www.")


def is_paywalled(url_or_host: str) -> bool:
    h = host(url_or_host) if "/" in url_or_host else url_or_host.lower().removeprefix("www.")
    return any(h == d or h.endswith("." + d) for d in PAYWALLED)


def day_dir(day: str, beat: str) -> Path:
    p = ROOT / day / beat
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path: Path, obj) -> None:  # noqa: ANN001
    path.parent.mkdir(parents=True, exist_ok=True)
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    path.write_text(json.dumps(data, indent=1, default=str))


def read_arm(path: Path) -> ArmResult:
    return ArmResult.model_validate_json(path.read_text())
