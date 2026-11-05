"""Trace writing: `eval/runs/<run_id>/` (TDD §16.2 step 4).

Layout:
  manifest.json            the manifest as run
  traces/<profile>.jsonl   one DayRecord per (user, day), in day order
  traces/events.jsonl      phase events with simulated timestamps
  failures.jsonl           exceptions per (user, day)
  summary.json             counts, failures, simulator call counts, wall time
  digests/<date>_<beat>.json  (only for a digests run)

DayRecords contain no wall-clock values, so a warm-cache re-run is byte-identical (TDD §16.3).
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.contracts.brief import BriefScript, Candidate, SourcePack
from app.contracts.digest import Digest
from app.contracts.memory import MemorySnapshot
from eval.sim.simulator import SessionLog

RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"


class DayRecord(BaseModel):
    run_id: str
    experiment: str
    seed: int
    profile_id: str
    user_id: str
    day: date
    day_index: int
    brief: BriefScript
    packs: list[SourcePack] = []
    candidates: list[Candidate] = []
    session: SessionLog
    ratings: dict[str, int] = {}
    agent_turns: list[dict[str, Any]] = []
    snapshot: MemorySnapshot | None = None
    followups: list[dict[str, Any]] = []
    reused: dict[str, str] = {}  # which parts were reused from which run


class TraceWriter:
    def __init__(self, root: Path, run_id: str):
        self.dir = root / run_id
        (self.dir / "traces").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _append(self, path: Path, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, sort_keys=True, default=str)
        with self._lock, path.open("a") as f:
            f.write(line + "\n")

    def manifest(self, data: dict[str, Any]) -> None:
        (self.dir / "manifest.json").write_text(json.dumps(data, indent=2, sort_keys=True, default=str))

    def day(self, rec: DayRecord) -> None:
        self._append(self.dir / "traces" / f"{rec.profile_id}.jsonl", rec.model_dump(mode="json"))

    def event(self, **kw: Any) -> None:
        self._append(self.dir / "traces" / "events.jsonl", kw)

    def failure(self, **kw: Any) -> None:
        self._append(self.dir / "failures.jsonl", kw)

    def summary(self, data: dict[str, Any]) -> None:
        (self.dir / "summary.json").write_text(json.dumps(data, indent=2, sort_keys=True, default=str))


def digest_path(root: Path, digests_run_id: str, d: date, beat: str) -> Path:
    return root / digests_run_id / "digests" / f"{d.isoformat()}_{beat}.json"


def save_digest(root: Path, digests_run_id: str, dg: Digest) -> None:
    p = digest_path(root, digests_run_id, dg.at.date(), dg.beat)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dg.model_dump_json(indent=1))


def load_digest(root: Path, digests_run_id: str, d: date, beat: str) -> Digest | None:
    p = digest_path(root, digests_run_id, d, beat)
    return Digest.model_validate_json(p.read_text()) if p.exists() else None


def load_day_records(root: Path, run_id: str) -> dict[tuple[str, date], DayRecord]:
    out = {}
    for p in sorted((root / run_id / "traces").glob("*.jsonl")):
        if p.name == "events.jsonl":
            continue
        for line in p.read_text().splitlines():
            r = DayRecord.model_validate_json(line)
            out[(r.profile_id, r.day)] = r
    return out
