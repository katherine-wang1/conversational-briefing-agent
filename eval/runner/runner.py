"""Replay runner (TDD §16.2): newsroom phase, then users in parallel with days in order.

Per user, per day:
  1. clock = brief_time - cos_lead_min  -> cos_daily (or reuse another run's brief / scores)
  2. clock = brief_time                 -> simulated session (text mode) + separate sim.rate call
  3.                                    -> memory_update -> snapshot
  4. clock += followup_delay_h          -> followup_research
Each (user, day) writes one DayRecord. A failure on one day is recorded and the user continues.
Grading (§18) runs afterwards over the traces.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import loader
from app.contracts.digest import Digest
from app.contracts.llm import ModelClient
from eval.runner.interfaces import AgentEntryPoints, DailyBrief, RunContext, SimClock, day_start
from eval.runner.manifest import RunManifest
from eval.runner.traces import RUNS_DIR, DayRecord, TraceWriter, load_day_records, load_digest, save_digest
from eval.sim.profile import SimProfile
from eval.sim.simulator import Simulator


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parent).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is best effort
        return None


def brief_datetime(profile: SimProfile, d: date) -> datetime:
    hh, mm = (int(x) for x in profile.brief_time.split(":"))
    return day_start(d, 0) + timedelta(hours=hh, minutes=mm)


class Runner:
    def __init__(self, manifest: RunManifest, agent: AgentEntryPoints, sim_client: ModelClient,
                 runs_dir: Path = RUNS_DIR):
        self.m = manifest
        self.agent = agent
        self.sim_client = sim_client
        self.root = runs_dir
        self.exp = loader.experiment(manifest.experiment)
        self.writer = TraceWriter(runs_dir, manifest.run_id)
        self._source: dict[str, dict[tuple[str, date], DayRecord]] = {}

    # -- phases --

    async def newsroom_phase(self) -> dict[date, list[Digest]]:
        """Once per window, shared by all runs: reuse stored digests when present."""
        out: dict[date, list[Digest]] = {}
        clock = SimClock(day_start(self.m.window.start, self.m.newsroom_hour_utc))
        ctx = self._ctx(clock)
        for d in self.m.window.dates():
            out[d] = []
            for beat in self.m.beats:
                dg = load_digest(self.root, self.m.digests_run_id, d, beat)
                if dg is None:
                    if self.m.reuse.digests_from:
                        raise FileNotFoundError(f"digests_from={self.m.reuse.digests_from} has no digest for "
                                                f"{d} {beat}")
                    at = day_start(d, self.m.newsroom_hour_utc)
                    clock.set(at)
                    dg = await self.agent.newsroom_run(beat, at, ctx)
                    save_digest(self.root, self.m.digests_run_id, dg)
                    self.writer.event(phase="newsroom", day=d.isoformat(), beat=beat, at=at.isoformat())
                out[d].append(dg)
        return out

    async def user_phase(self, digests: dict[date, list[Digest]]) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(self.m.concurrency)

        async def one(p: SimProfile) -> dict[str, Any]:
            async with sem:
                return await self.run_user(p, digests)

        return list(await asyncio.gather(*(one(p) for p in self.m.profiles())))

    async def run_user(self, profile: SimProfile, digests: dict[date, list[Digest]]) -> dict[str, Any]:
        days = self.m.window.dates()
        clock = SimClock(brief_datetime(profile, days[0]) - timedelta(minutes=self.m.cos_lead_min + 1))
        ctx = self._ctx(clock)
        user_id = await self.agent.create_user(profile.id, profile.onboarding, ctx)
        ok = failed = 0
        for i, d in enumerate(days):
            try:
                rec = await self.run_day(profile, user_id, i, d, digests[d], ctx)
                self.writer.day(rec)
                ok += 1
            except Exception as e:  # noqa: BLE001 - record and continue with the next day
                failed += 1
                self.writer.failure(profile_id=profile.id, day=d.isoformat(), error=f"{type(e).__name__}: {e}")
        return {"profile_id": profile.id, "user_id": user_id, "days_ok": ok, "days_failed": failed}

    async def run_day(self, profile: SimProfile, user_id: str, i: int, d: date, digests: list[Digest],
                      ctx: RunContext) -> DayRecord:
        bt = brief_datetime(profile, d)
        reused: dict[str, str] = {}
        ctx.memory_override = None
        ctx.reused_candidates = None
        if src := self.m.reuse.day_wise_counterfactual_from:
            prev = self._source_record(src, profile.id, d - timedelta(days=1))
            ctx.memory_override = prev.snapshot if prev else None
            reused["memory"] = src
        # 1. chief of staff
        ctx.clock.set(bt - timedelta(minutes=self.m.cos_lead_min))
        if src := self.m.reuse.briefs_from:
            rec = self._source_record(src, profile.id, d)
            if rec is None:
                raise FileNotFoundError(f"briefs_from={src}: no brief for {profile.id} {d}")
            daily = DailyBrief(brief=rec.brief, packs=rec.packs, candidates=rec.candidates)
            reused["brief"] = src
        else:
            if src := self.m.reuse.reuse_scores_from:
                rec = self._source_record(src, profile.id, d)
                ctx.reused_candidates = rec.candidates if rec else None
                reused["candidates"] = src
            daily = await self.agent.cos_daily(user_id, d, digests, ctx)
        self.writer.event(phase="cos_daily", profile=profile.id, day=d.isoformat(), at=ctx.clock.now().isoformat())
        # 2. session
        ctx.clock.set(bt)
        session_id, transport = await self.agent.open_session(user_id, daily, ctx)
        sim = Simulator(profile, self.sim_client, day=i, seed=self.m.seed, run_id=self.m.run_id,
                        cache_namespace=f"sim-s{self.m.seed}")
        log = await sim.run_session(daily.brief, transport, session_id)
        ratings = await sim.rate(daily.brief)
        turns = await self.agent.session_turns(session_id, ctx)
        ctx.clock.advance(timedelta(seconds=log.time_used_s))
        # 3. memory
        snapshot = await self.agent.memory_update(user_id, session_id, ctx)
        # 4. follow-ups
        ctx.clock.set(bt + timedelta(hours=self.m.followup_delay_h))
        followups = await self.agent.followup_research(user_id, ctx)
        return DayRecord(
            run_id=self.m.run_id, experiment=self.m.experiment, seed=self.m.seed, profile_id=profile.id,
            user_id=user_id, day=d, day_index=i, brief=daily.brief, packs=daily.packs, candidates=daily.candidates,
            session=log, ratings=ratings, agent_turns=turns, snapshot=snapshot, followups=followups, reused=reused,
        )

    async def run(self) -> dict[str, Any]:
        t0 = time.monotonic()
        self.writer.manifest({**self.m.model_dump(mode="json"), "git_sha": _git_sha(),
                              "experiment_cfg": self.exp.model_dump()})
        digests = await self.newsroom_phase()
        users = await self.user_phase(digests)
        summary = {
            "run_id": self.m.run_id, "experiment": self.m.experiment, "seed": self.m.seed,
            "users": users, "days_ok": sum(u["days_ok"] for u in users),
            "days_failed": sum(u["days_failed"] for u in users),
            "wall_s": round(time.monotonic() - t0, 2),
        }
        self.writer.summary(summary)
        return summary

    # -- helpers --

    def _ctx(self, clock: SimClock) -> RunContext:
        return RunContext(run_id=self.m.run_id, experiment=self.exp, seed=self.m.seed, clock=clock,
                          cache_namespace=self.m.namespace, digests_run_id=self.m.digests_run_id)

    def _source_record(self, run_id: str, profile_id: str, d: date) -> DayRecord | None:
        if run_id not in self._source:
            self._source[run_id] = load_day_records(self.root, run_id)
        return self._source[run_id].get((profile_id, d))


async def run_manifest(manifest: RunManifest, agent: AgentEntryPoints, sim_client: ModelClient,
                       runs_dir: Path = RUNS_DIR) -> dict[str, Any]:
    return await Runner(manifest, agent, sim_client, runs_dir).run()
