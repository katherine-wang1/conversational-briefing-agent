"""Load the T7 injection set, assign canaries, and turn documents into archive `Document`s.

AUTHORED TEST CONTENT: the documents are hand-written fictional articles (see injection_v1.yaml).
They are meant to be inserted into a *copy* of the replay archive for a dedicated T7 run, never into
the archive used for the main results.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from app.contracts.sources import Document, HeadlineObservation
from eval.graders.injection import ForbiddenCall

HERE = Path(__file__).resolve().parent
Style = Literal[
    "direct", "hidden_text", "fake_system", "tool_bait", "authority", "exfiltration", "score_manipulation"
]


class InjectionDoc(BaseModel):
    id: str
    style: Style
    target: str  # agent role most likely to read it
    subtopic: str
    source: str
    title: str
    body: str
    planted_instruction: str
    expected_safe_behavior: str
    forbidden_tool_calls: list[ForbiddenCall] = []
    canary: str = ""
    authored: str = "hand-authored test content (W-G); fictional; not collected data"

    @property
    def url(self) -> str:
        return f"https://{self.source}/{self.id}"


def canary_for(doc_id: str, version: int) -> str:
    h = hashlib.sha256(f"injection-v{version}:{doc_id}".encode()).hexdigest()[:6].upper()
    return f"ZQX-{doc_id.upper().replace('_', '')}-{h}"


@lru_cache
def load_injection_set(version: int = 1) -> tuple[InjectionDoc, ...]:
    raw = yaml.safe_load((HERE / f"injection_v{version}.yaml").read_text())
    out = []
    for d in raw["documents"]:
        can = canary_for(d["id"], version)

        def fill(s: str, c: str = can) -> str:
            return s.replace("{CANARY}", c)

        d = {
            **d,
            "body": fill(d["body"]),
            "planted_instruction": fill(d["planted_instruction"]),
            "forbidden_tool_calls": [
                {**f, "arg_contains": fill(f["arg_contains"]) if f.get("arg_contains") else None}
                for f in d.get("forbidden_tool_calls", [])
            ],
        }
        out.append(InjectionDoc(**d, canary=can))
    return tuple(out)


def to_archive(
    docs: tuple[InjectionDoc, ...], start: datetime, spread_days: int = 30, beat: str = "ai"
) -> tuple[list[Document], list[HeadlineObservation]]:
    """Documents + headline observations for the injection run, one document per day slot in
    order, published at 05:00 so they are visible to that day's 06:00 newsroom run."""
    documents, observations = [], []
    for i, d in enumerate(docs):
        at = start + timedelta(days=i % spread_days, hours=5)
        documents.append(
            Document(
                url_hash=hashlib.sha256(d.url.encode()).hexdigest()[:16],
                url=d.url,
                source=d.source,
                title=d.title,
                published_at=at,
                available_at=at,
                fetched_at=at,
                text=d.body,
                extractor_ver="injection-v1",
            )
        )
        observations.append(
            HeadlineObservation(
                source_id="injection_test",
                beat=beat,
                observed_at=at,
                url=d.url,
                title=d.title,
                rank=1 + i % 10,
                snippet=d.body[:280],
                published_at=at,
            )
        )
    return documents, observations


def summary(docs: tuple[InjectionDoc, ...]) -> dict[str, dict[str, int]]:
    return {"style": dict(Counter(d.style for d in docs)), "target": dict(Counter(d.target for d in docs))}
