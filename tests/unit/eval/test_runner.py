"""Runner skeleton with fakes: manifest, phases, reuse, simulated clock, traces, determinism."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from eval.runner.__main__ import main as cli
from eval.runner.fakes import FakeAgent, scripted_sim_client
from eval.runner.manifest import REPO_ROOT, RunManifest, check_namespaces, load_manifest
from eval.runner.runner import Runner
from eval.runner.traces import load_day_records

MANIFESTS = Path(__file__).resolve().parents[3] / "eval" / "runner" / "manifests"


def M(**kw) -> RunManifest:
    base = dict(
        run_id="t-full-s1",
        experiment="full",
        seed=1,
        window={"start": "2026-10-01", "days": 3, "slice": "test"},
        users=["sim_t01", "sim_t09"],
        concurrency=2,
    )
    base.update(kw)
    return RunManifest(**base)


def test_example_manifests_load_and_namespaces_unique():
    ms = [load_manifest(p) for p in sorted(MANIFESTS.glob("*.yaml"))]
    assert {m.run_id for m in ms} >= {"test-full-s1", "test-b4-s1"}
    assert check_namespaces(ms) == []
    assert len(next(m for m in ms if m.run_id == "test-full-s1").profiles()) == 12
    assert len(next(m for m in ms if m.run_id == "dev-full-s1").profiles()) == 6


def test_manifest_validation():
    with pytest.raises(ValidationError):
        M(experiment="nope")
    with pytest.raises(ValidationError):
        M(reuse={"briefs_from": "a", "reuse_scores_from": "b"})
    a, b = M(run_id="x1", cache_namespace="ns"), M(run_id="x2", seed=2, cache_namespace="ns")
    assert check_namespaces([a, b])  # one namespace per seed
    assert REPO_ROOT.joinpath("config").is_dir()


async def test_run_writes_traces_and_reuses_digests(tmp_path):
    agent = FakeAgent()
    s = await Runner(M(), agent, scripted_sim_client(), runs_dir=tmp_path).run()
    assert s["days_ok"] == 6 and s["days_failed"] == 0
    recs = load_day_records(tmp_path, "t-full-s1")
    assert len(recs) == 6
    r = recs[("sim_t09", date(2026, 10, 2))]
    assert r.day_index == 1 and r.snapshot is not None and r.session.profile_id == "sim_t09"
    assert set(r.ratings) == {s.story_ref for s in r.brief.stories}
    # newsroom ran once per day; a second run with digests_from reuses them
    assert sum(1 for c in agent.calls if c[0] == "newsroom_run") == 3
    agent2 = FakeAgent()
    await Runner(
        M(run_id="t-b1-s1", experiment="b1_importance_only", reuse={"digests_from": "t-full-s1-digests"}),
        agent2,
        scripted_sim_client(),
        runs_dir=tmp_path,
    ).run()
    assert not any(c[0] == "newsroom_run" for c in agent2.calls)


async def test_days_are_sequential_per_user_and_clock_is_simulated(tmp_path):
    agent = FakeAgent()
    await Runner(M(users=["sim_t01"]), agent, scripted_sim_client(), runs_dir=tmp_path).run()
    order = [c for c in agent.calls if c[0] in ("cos_daily", "memory_update", "followup_research")]
    assert [c[0] for c in order[:3]] == ["cos_daily", "memory_update", "followup_research"]
    days = [c[1].split(":")[1] for c in agent.calls if c[0] == "cos_daily"]
    assert days == sorted(days)
    recs = load_day_records(tmp_path, "t-full-s1")
    snaps = [recs[("sim_t01", d)].snapshot.as_of for d in (date(2026, 10, 1), date(2026, 10, 2))]
    assert snaps == [date(2026, 10, 1), date(2026, 10, 2)]  # memory_update saw the simulated clock


async def test_briefs_from_and_counterfactual_reuse(tmp_path):
    await Runner(M(), FakeAgent(), scripted_sim_client(), runs_dir=tmp_path).run()
    agent = FakeAgent()
    m = M(
        run_id="t-b4-s1",
        experiment="b4_no_prefetch",
        reuse={
            "digests_from": "t-full-s1-digests",
            "briefs_from": "t-full-s1",
            "day_wise_counterfactual_from": "t-full-s1",
        },
    )
    runner = Runner(m, agent, scripted_sim_client(), runs_dir=tmp_path)
    s = await runner.run()
    fails = tmp_path / "t-b4-s1" / "failures.jsonl"
    assert s["days_failed"] == 0, fails.read_text()
    assert not any(c[0] == "cos_daily" for c in agent.calls)  # briefs reused
    src = load_day_records(tmp_path, "t-full-s1")
    out = load_day_records(tmp_path, "t-b4-s1")
    k = ("sim_t01", date(2026, 10, 2))
    assert out[k].brief == src[k].brief
    assert out[k].reused == {"brief": "t-full-s1", "memory": "t-full-s1"}


async def test_failure_on_a_day_is_recorded_and_run_continues(tmp_path):
    agent = FakeAgent()
    orig = agent.cos_daily

    async def flaky(user_id, local_date, digests, ctx):
        if local_date == date(2026, 10, 2):
            raise RuntimeError("boom")
        return await orig(user_id, local_date, digests, ctx)

    agent.cos_daily = flaky  # type: ignore[method-assign]
    s = await Runner(M(users=["sim_t01"]), agent, scripted_sim_client(), runs_dir=tmp_path).run()
    assert (s["days_ok"], s["days_failed"]) == (2, 1)
    assert "boom" in (tmp_path / "t-full-s1" / "failures.jsonl").read_text()


async def test_rerun_is_byte_identical(tmp_path):
    for d in ("a", "b"):
        await Runner(M(), FakeAgent(), scripted_sim_client(), runs_dir=tmp_path / d).run()
    for name in ("sim_t01.jsonl", "sim_t09.jsonl"):
        a = (tmp_path / "a" / "t-full-s1" / "traces" / name).read_text()
        b = (tmp_path / "b" / "t-full-s1" / "traces" / name).read_text()
        assert a == b


async def test_grade_run_over_fake_traces(tmp_path):
    from eval.graders.from_run import grade_run
    from eval.sim.profile import load_panel

    await Runner(M(), FakeAgent(), scripted_sim_client(), runs_dir=tmp_path).run()
    recs = load_day_records(tmp_path, "t-full-s1")
    assert any(r.session.questions for r in recs.values())  # the scripted simulator asked something
    profiles = {p.id: p for p in load_panel()}
    imp = {"st-2026-10-01-0": date(2026, 10, 1)}
    g = grade_run("t-full-s1", profiles, runs_dir=tmp_path, important_first_seen=imp)
    assert g["user_days"] == 6 and set(g["relevance"]) == {"sim_t01", "sim_t09"}
    assert g["prefetch_hit_rate"]["n"] >= 1
    assert g["specificity"]["n"] > 0
    assert 0 <= g["importance_recall"]["mean"] <= 1
    assert set(g["interest_recovery"]) == {"sim_t01", "sim_t09"}


def test_cli_validate_and_fake_run(tmp_path, capsys):
    assert cli(["validate", "--manifest", str(MANIFESTS / "test-full-s1.yaml")]) == 0
    mf = tmp_path / "m.yaml"
    mf.write_text(
        "run_id: smoke\nexperiment: full\nseed: 3\nwindow: {start: 2026-10-01, days: 2}\nusers: [sim_t02]\n"
    )
    assert cli(["run", "--manifest", str(mf), "--runs-dir", str(tmp_path)]) == 2  # refuses without an agent
    assert cli(["run", "--manifest", str(mf), "--fake", "--runs-dir", str(tmp_path)]) == 0
    assert (tmp_path / "smoke" / "summary.json").exists()
