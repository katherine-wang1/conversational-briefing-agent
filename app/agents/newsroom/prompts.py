"""Load newsroom prompts from app/agents/prompts/*.md (front-matter: role, version; TDD §19.3)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@dataclass(frozen=True)
class Prompt:
    prompt_id: str  # file stem, e.g. "newsroom.score"
    role: str
    version: str
    template: str
    sha256: str  # hash of the whole file (front-matter + body)

    def render(self, **values: object) -> str:
        """Replace `{{name}}` placeholders. Unknown placeholders raise, so templates can't drift."""
        out = self.template
        for k, v in values.items():
            out = out.replace("{{" + k + "}}", str(v))
        if "{{" in out:
            start = out.index("{{")
            raise KeyError(f"unfilled placeholder in {self.prompt_id}: {out[start : start + 40]}")
        return out


@lru_cache
def load_prompt(prompt_id: str) -> Prompt:
    path = PROMPTS_DIR / f"{prompt_id}.md"
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"{path} has no front-matter")
    _, fm, body = raw.split("---\n", 2)
    meta = yaml.safe_load(fm) or {}
    return Prompt(
        prompt_id=prompt_id,
        role=str(meta["role"]),
        version=str(meta["version"]),
        template=body.strip() + "\n",
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
