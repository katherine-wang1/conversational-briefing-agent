"""Prompt files: `app/agents/prompts/<name>.md` with YAML front-matter (`role`, `version`).

The prompt hash is recorded in traces (TDD §19.3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    role: str
    version: str
    body: str
    sha: str


def parse_prompt(name: str, raw: str) -> Prompt:
    if not raw.startswith("---"):
        raise ValueError(f"prompt {name} has no front-matter")
    _, fm, body = raw.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    for key in ("role", "version"):
        if key not in meta:
            raise ValueError(f"prompt {name} front-matter is missing {key!r}")
    sha = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return Prompt(
        name=name, role=str(meta["role"]), version=str(meta["version"]), body=body.strip() + "\n", sha=sha
    )


@lru_cache
def load_prompt(name: str) -> Prompt:
    """Load `app/agents/prompts/<name>.md`."""
    return parse_prompt(name, (PROMPTS_DIR / f"{name}.md").read_text())
