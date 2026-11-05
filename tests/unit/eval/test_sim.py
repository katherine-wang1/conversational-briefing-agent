"""Simulated users: panel validity, coverage, profile-at-day, and the simulator loop (with fakes)."""

from __future__ import annotations

import math

import pytest

from eval.runner.fakes import FakeModelClient, FakeVoiceAgent
from eval.sim import coverage
from eval.sim.profile import SimProfile, load_panel, panel_split, validate_taxonomy
from eval.sim.simulator import Simulator, latent_leak

from .conftest import allowed_actions, make_brief


def test_panel_sizes_and_coverage_clean():
    assert len(panel_split("test")) == 12
    assert len(panel_split("dev")) == 6
    assert len(panel_split("scenario")) == 4
    assert [p.id for p in panel_split("kate")] == ["sim_kate"]
    assert coverage.check_coverage() == []


def test_every_profile_uses_ai_subtopics_and_weights_sum_to_one():
    for p in load_panel():
        assert validate_taxonomy(p) == []
        for day in (0, 5, 12, 20, 29):
            assert math.isclose(sum(p.weights_at(day).values()), 1.0, abs_tol=1e-9)


def test_coverage_detects_a_gap():
    test = [p for p in load_panel() if p.split == "test" and p.role != "journalist"]
    m = coverage.matrix(test)
    assert "journalist" not in m["role"]


def _profile(**over) -> SimProfile:
    base = load_panel()[0].model_dump()
    base.update(over)
    return SimProfile(**base)


def test_drift_add_fade_shift():
    p = next(p for p in load_panel() if p.id == "sim_t09")
    w0, w12, w25 = p.weights_at(0), p.weights_at(12), p.weights_at(25)
    assert "policy-and-regulation" not in w0
    assert w12["policy-and-regulation"] == pytest.approx(0.2 / 1.2)
    assert "open-models" not in w25  # faded over 3 days from day 20
    mid = p.weights_at(20)
    assert 0 < mid["open-models"] < w12["open-models"]
    s = next(p for p in load_panel() if p.id == "sim_s_drift")
    assert set(s.weights_at(9)) == {"agents-and-tools", "products-and-apps"}
    assert set(s.weights_at(10)) == {"chips-and-compute", "business-and-funding"}
    targets = {(d, sub): t for d, sub, t in s.drift_targets()}
    assert targets[(10, "agents-and-tools")] == 0.0
    assert targets[(10, "chips-and-compute")] == pytest.approx(0.7)


def test_latent_leak_guard():
    p = next(p for p in load_panel() if p.id == "sim_t09")
    assert latent_leak("What about speculative decoding?", p, heard_lower="") == ["speculative decoding"]
    assert (
        latent_leak("What about speculative decoding?", p, heard_lower="... speculative decoding ...") == []
    )


async def _run(profile_id: str, prefer: list[str], text="Who funded that?", brief=None, seed=1, day=0):
    p = next(p for p in load_panel() if p.id == profile_id)
    client = FakeModelClient({"sim.user": _resp(prefer, text)})
    brief = brief or make_brief()
    agent = FakeVoiceAgent(brief)
    sim = Simulator(p, client, day=day, seed=seed)
    log = await sim.run_session(brief, agent, "sess1")
    return log, client, agent


def _resp(prefer, text):
    def fn(req):
        allowed = allowed_actions(req)
        for a in prefer:
            if a in allowed:
                return {"action": a, "text": text}
        return {"action": allowed[0], "text": ""}

    return fn


async def test_relaxed_listener_lets_everything_play_and_ends_naturally():
    log, client, agent = await _run("sim_t10", ["let_play", "end"])
    assert log.brief_completed and log.end_reason == "natural"
    assert all(s.heard == "full" for s in log.stories)
    assert log.questions == []
    assert agent.received[0].type == "hello"
    assert agent.received[-1].type == "control" and agent.received[-1].action == "end"
    assert all(r.role == "sim.user" for r in client.requests)


async def test_time_budget_cuts_session_short():
    long_brief = make_brief(n_stories=8, sentences_per_story=4)
    log, _, _ = await _run("sim_s_rushed", ["let_play"], brief=long_brief)
    assert log.end_reason == "cut_short"
    assert not log.brief_completed
    assert log.time_used_s <= log.time_budget_s
    assert any(s.heard == "none" for s in log.stories)


async def test_questions_capped_by_sampled_max_and_answers_logged():
    log, _, agent = await _run("sim_t10", ["interrupt", "ask", "let_play", "end"])
    assert len(log.questions) == log.max_questions <= 3
    for q in log.questions:
        assert q.answer_text
        assert q.target_story_id is None or q.target_story_id.startswith("st")
    utterances = [m for m in agent.received if m.type == "utterance"]
    assert len(utterances) == len(log.questions)
    interrupts = [m for m in agent.received if m.type == "interrupt"]
    assert interrupts and all(i.segment_id for i in interrupts)


async def test_unanswerable_share_is_about_fifteen_percent():
    # Over many sessions the code-sampled 'unanswerable' instruction lands on ~15% of questions.
    n_q = n_un = 0
    for seed in range(120):
        log, _, _ = await _run("sim_t10", ["interrupt", "ask", "let_play", "end"], seed=seed)
        n_q += len(log.questions)
        n_un += sum(q.intended_unanswerable for q in log.questions)
    assert n_q > 200
    assert 0.08 < n_un / n_q < 0.23


async def test_unanswerable_question_is_bookmarked_by_fake_agent():
    log, _, _ = await _run(
        "sim_t10", ["interrupt", "let_play", "end"], text="That is unknowable: what's the margin?"
    )
    assert log.questions and all(q.bookmarked for q in log.questions)


async def test_latent_term_never_spoken_unless_heard():
    log, client, _ = await _run(
        "sim_t09", ["interrupt", "ask", "let_play", "end"], text="Anything on speculative decoding?"
    )
    assert log.latent_leaks_blocked > 0
    assert not any("speculative decoding" in t.text.lower() for t in log.turns if t.actor == "user")


async def test_latent_interests_in_prompt_are_marked_private():
    _, client, _ = await _run("sim_t09", ["let_play"])
    prompt = client.requests[0].messages[-1].content
    assert "PRIVATE INTERESTS" in prompt and "speculative decoding" in prompt
    # no agent internals leak into the simulator prompt
    for word in ("relevance", "importance", "slot", "why_tag", "memory"):
        assert word not in prompt.split("PRIVATE INTERESTS")[1].lower() or word == "memory"


async def test_skip_and_save_update_story_outcomes():
    log, _, agent = await _run("sim_t02", ["skip"])
    assert all(s.skipped for s in log.stories)
    assert any(m.type == "control" and m.action == "skip" for m in agent.received)
    log2, _, _ = await _run("sim_t10", ["save", "let_play"], text="Save that.")
    assert any(s.saved and s.engaged for s in log2.stories)


async def test_session_is_deterministic_for_same_seed():
    a, _, _ = await _run("sim_t06", ["interrupt", "ask", "let_play", "end"], seed=7, day=3)
    b, _, _ = await _run("sim_t06", ["interrupt", "ask", "let_play", "end"], seed=7, day=3)
    assert a.model_dump() == b.model_dump()


async def test_rating_is_a_separate_call(brief, fake_client_factory):
    client = fake_client_factory(["let_play"])
    p = load_panel()[0]
    ratings = await Simulator(p, client, day=0, seed=1).rate(brief)
    assert ratings == {"st0": 3, "st1": 4, "st2": 3}
    assert [r.role for r in client.requests] == ["sim.rate"]
