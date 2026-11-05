"""What the runner needs from the agent track, as interfaces.

The runner is written against `AgentEntryPoints`; the real implementation (an adapter over
W-C newsroom, W-D chief of staff / memory / follow-up, W-E voice session server in text mode) is
plugged in with `--agent module:factory` once those exist. `eval/runner/fakes.FakeAgent` implements
it for tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

from app.config.loader import ExperimentCfg
from app.contracts.brief import BriefScript, Candidate, SelectedStory, SourcePack
from app.contracts.digest import Digest
from app.contracts.memory import MemorySnapshot
from eval.sim.simulator import SessionTransport


class SimClock:
    """Simulated clock for replay. Agents must read time from `ctx.clock.now()`, never the wall clock."""

    def __init__(self, now: datetime):
        if now.tzinfo is None:
            raise ValueError("SimClock needs an aware datetime")
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        self._now = t

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class DailyBrief(BaseModel):
    """cos_daily output the runner needs (TDD §8.2 C1-C10)."""

    brief: BriefScript
    packs: list[SourcePack] = []
    candidates: list[Candidate] = []
    selection: list[SelectedStory] = []


@dataclass
class RunContext:
    """Per-user execution context passed to every entry point."""

    run_id: str
    experiment: ExperimentCfg
    seed: int
    clock: SimClock
    cache_namespace: str
    digests_run_id: str
    mode: str = "replay"
    # reuse (TDD §16.2)
    memory_override: MemorySnapshot | None = None  # day-wise counterfactual: start the day from this
    reused_candidates: list[Candidate] | None = None  # B3: reuse scored candidates; run C5 onward
    extra: dict[str, Any] = field(default_factory=dict)


class AgentEntryPoints(Protocol):
    async def newsroom_run(self, beat: str, at: datetime, ctx: RunContext) -> Digest: ...

    async def create_user(self, profile_id: str, onboarding_text: str, ctx: RunContext) -> str:
        """Create a synthetic account (profiles.type = synthetic) under ctx.run_id; returns user_id.
        Onboarding goes through the normal onboarding path (TDD §9.4)."""
        ...

    async def cos_daily(
        self, user_id: str, local_date: date, digests: list[Digest], ctx: RunContext
    ) -> DailyBrief: ...

    async def open_session(
        self, user_id: str, daily: DailyBrief, ctx: RunContext
    ) -> tuple[str, SessionTransport]:
        """Start a text-mode session (no TTS) against the voice agent for `daily.brief`; returns
        (session_id, transport). With `briefs_from` reuse the brief comes from another run, so the
        adapter must not assume it produced it. Under B4 the adapter withholds `daily.packs`."""
        ...

    async def session_turns(self, session_id: str, ctx: RunContext) -> list[dict[str, Any]]:
        """Per agent turn: {turn_id, tools: [tool names], ttft_ms, citations}. Needed by the pre-fetch
        and latency graders (tool use is not visible over the session protocol)."""
        ...

    async def memory_update(self, user_id: str, session_id: str, ctx: RunContext) -> MemorySnapshot: ...

    async def followup_research(self, user_id: str, ctx: RunContext) -> list[dict[str, Any]]:
        """Run bookmarked-question research; returns [{question_id, answered_at, answer, citations}]."""
        ...


def day_start(d: date, hour: int) -> datetime:
    from datetime import UTC

    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC)
