"""Memory rules engine (TDD §9.3): every rule, both ablations, both baseline modes."""

from datetime import timedelta

import pytest

from app.agents.memory.config import MemoryConfig, memory_config
from app.agents.memory.rules import (
    apply_memory_update,
    concentration_guard,
    decay,
    implicit_estimate,
    opportunity,
    skip_rule_met,
)
from app.config.loader import ExperimentFlags
from app.contracts.agent_io import SessionOutcome, SessionRecord, ShownStory, TranscriptTurn
from app.contracts.memory import (
    DeliveryChange,
    DeliveryPref,
    MemoryProposal,
    MemorySignal,
    MemorySnapshot,
    ProposalOp,
    SessionContext,
)
from tests.unit.cos.fixtures import NOW, TODAY, USER, interest

NOCAP = MemoryConfig(max_subtopic_share=1.0)
FULL_CTX = SessionContext(end_reason="natural", time_available_s=600, brief_length_s=180)
SHORT_CTX = SessionContext(end_reason="cut_short", time_available_s=120, brief_length_s=180)


def mem(*interests, delivery=None, as_of=TODAY, profile_text=None) -> MemorySnapshot:
    return MemorySnapshot(
        user_id=USER,
        as_of=as_of,
        interests=list(interests),
        delivery=delivery or [],
        profile_text=profile_text,
    )


def sig(type_, story_ref=None, explicit=False, turn_id=None, hint=None) -> MemorySignal:
    return MemorySignal(
        session_id="sess-1",
        type=type_,
        story_ref=story_ref,
        explicit=explicit,
        turn_id=turn_id,
        interest_hint=hint,
        created_at=NOW,
    )


def session(signals=(), shown=(), ctx=FULL_CTX, sid="sess-1", at=NOW, transcript=()) -> SessionRecord:
    return SessionRecord(
        session_id=sid,
        started_at=at,
        ctx=ctx,
        signals=list(signals),
        shown=list(shown),
        transcript=list(transcript),
    )


def story(ref, interest_ids, heard=True) -> ShownStory:
    return ShownStory(story_ref=ref, headline=ref, shown_on=TODAY, interest_ids=interest_ids, heard=heard)


def op(o, label, strength, signal, subtopic="agents-and-tools", **kw) -> ProposalOp:
    return ProposalOp(op=o, label=label, subtopic=subtopic, strength=strength, signal=signal, **kw)


def run(m, ops=(), s=None, cfg=NOCAP, flags=None, prior=(), delivery=()):
    return apply_memory_update(
        m,
        MemoryProposal(ops=list(ops), delivery=list(delivery)),
        s or session(),
        now=NOW,
        cfg=cfg,
        flags=flags,
        prior_sessions=prior,
    )


def w(m: MemorySnapshot, iid: str) -> float:
    return next(i.weight for i in m.interests if i.id == iid)


def get(m: MemorySnapshot, iid: str):
    return next((i for i in m.interests if i.id == iid), None)


A = interest("a", "agent memory", "agents-and-tools", 0.5)


def test_config_loads_from_agent_yaml():
    c = memory_config()
    assert c.eta_explicit == 0.30 and c.eta_implicit == 0.15
    assert c.implicit_strength == {"dive": 0.5, "followup": 0.3, "save_for_later": 0.4, "skip": 0.2}
    assert c.skip_rule.min_sessions == 2 and c.skip_rule.window == 4
    assert c.beta_prior.a == 1 and c.beta_prior.b == 2
    assert c.onboarding.weight == 0.4 and c.onboarding.confidence == 0.3
    assert c.budget_input_tokens == 20000


# ---- opportunity --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ctx,expected",
    [
        (SessionContext(end_reason="natural", time_available_s=600, brief_length_s=180), 1.0),
        (SessionContext(end_reason="natural", time_available_s=180, brief_length_s=180), 1.0),
        (SessionContext(end_reason="natural"), 1.0),
        (SessionContext(end_reason="natural", time_available_s=100, brief_length_s=180), 0.3),
        (SessionContext(end_reason="cut_short", time_available_s=600, brief_length_s=180), 0.3),
        (SessionContext(end_reason="idle"), 0.3),
        (SessionContext(end_reason="error"), 0.3),
        (SessionContext(), 0.3),
    ],
)
def test_opportunity(ctx, expected):
    assert opportunity(ctx, NOCAP) == expected


def test_opportunity_a3_always_one():
    assert opportunity(SHORT_CTX, NOCAP, session_ctx_weighting=False) == 1.0


def test_implicit_estimate_beta_prior():
    assert implicit_estimate(interest("x", "x", "other", shown_n=0, engaged_n=0), NOCAP) == pytest.approx(
        1 / 3
    )
    assert implicit_estimate(interest("x", "x", "other", shown_n=4, engaged_n=2), NOCAP) == pytest.approx(
        3 / 7
    )


# ---- decay --------------------------------------------------------------------------------------


def test_decay_half_lives_and_pins():
    m = mem(
        interest("imp", "i", "agents-and-tools", 0.8, source="implicit"),
        interest("exp", "e", "open-models", 0.8, source="explicit"),
        interest("onb", "o", "nuclear-fusion", 0.8, source="onboarding"),
        interest("exl", "x", "humanoids", 0.8, source="exploration"),
        interest("pin", "p", "solar-wind", 0.8, source="implicit", explicit_pin=True),
        as_of=TODAY - timedelta(days=21),
    )
    out, changes = decay(m, TODAY, NOCAP)
    assert w(out, "imp") == pytest.approx(0.4)
    assert w(out, "exl") == pytest.approx(0.4)
    assert w(out, "exp") == pytest.approx(0.8 * 0.5 ** (21 / 60))
    assert w(out, "onb") == pytest.approx(0.8 * 0.5 ** (21 / 60))
    assert w(out, "pin") == 0.8
    assert out.as_of == TODAY
    assert {c.action for c in changes} == {"decay"}


def test_decay_explicit_half_life_60():
    m = mem(interest("exp", "e", "open-models", 0.8, source="explicit"), as_of=TODAY - timedelta(days=60))
    assert w(decay(m, TODAY, NOCAP)[0], "exp") == pytest.approx(0.4)


def test_decay_zero_or_negative_days_is_noop():
    m = mem(A)
    assert decay(m, TODAY, NOCAP)[0] == m
    assert decay(m, TODAY - timedelta(days=1), NOCAP)[0].interests == m.interests


def test_apply_decays_before_ops():
    m = mem(
        interest("a", "agent memory", "agents-and-tools", 0.8, source="implicit"),
        as_of=TODAY - timedelta(days=21),
    )
    out, _ = run(m, [op("reinforce", "agent memory", "explicit", "more_like")])
    assert w(out, "a") == pytest.approx(0.4 + 0.3)


# ---- explicit ops -------------------------------------------------------------------------------


def test_explicit_reinforce_existing():
    out, rep = run(mem(A), [op("reinforce", "Agent Memory", "explicit", "more_like", evidence=["t1"])])
    i = get(out, "a")
    assert i.weight == pytest.approx(0.8)
    assert i.confidence >= 0.8
    assert i.last_reinforced_at == NOW
    assert "t1" in i.evidence and "sess-1" in i.evidence
    assert any(c.action == "reinforce" for c in rep.changes)


def test_explicit_add_new_interest():
    out, _ = run(
        mem(A),
        [
            op(
                "add",
                "Figure AI",
                "explicit",
                "keep_posted",
                subtopic="humanoids",
                entity="Figure AI",
                kind="keep_posted",
            )
        ],
    )
    new = [i for i in out.interests if i.id != "a"][0]
    assert new.weight == pytest.approx(0.3)
    assert new.source == "explicit" and new.kind == "keep_posted" and new.entity == "Figure AI"
    assert new.confidence == pytest.approx(0.8)
    assert new.id.startswith("i_")


def test_match_by_entity_before_label():
    m = mem(interest("acme", "Acme Corp news", "open-models", 0.3, entity="Acme"))
    out, _ = run(m, [op("reinforce", "acme models", "explicit", "more_like", entity="ACME")])
    assert len(out.interests) == 1 and w(out, "acme") == pytest.approx(0.6)


def test_explicit_weaken_and_floor_at_zero():
    out, _ = run(mem(A), [op("weaken", "agent memory", "explicit", "less_like")])
    assert w(out, "a") == pytest.approx(0.2)
    out, _ = run(out, [op("weaken", "agent memory", "explicit", "less_like")])
    assert w(out, "a") == 0.0


def test_explicit_weaken_unknown_is_ignored():
    out, rep = run(mem(A), [op("weaken", "crypto", "explicit", "less_like")])
    assert out.interests == mem(A).interests
    assert rep.changes[-1].action == "ignored"


def test_pin_unpin_remove():
    out, _ = run(mem(A), [op("pin", "agent memory", "explicit", "more_like")])
    assert get(out, "a").explicit_pin and w(out, "a") == pytest.approx(0.5)
    out, _ = run(out, [op("unpin", "agent memory", "explicit", "more_like")])
    assert not get(out, "a").explicit_pin
    out, _ = run(
        mem(A.model_copy(update={"explicit_pin": True})),
        [op("remove", "agent memory", "explicit", "stop_covering")],
    )
    assert out.interests == []


def test_pin_new_interest_gets_explicit_weight():
    out, _ = run(mem(), [op("pin", "robot hands", "explicit", "more_like", subtopic="robotics-hardware")])
    assert out.interests[0].explicit_pin and out.interests[0].weight == pytest.approx(0.3)


# ---- implicit ops -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signal,strength",
    [("dive", 0.5), ("followup", 0.3), ("save_for_later", 0.4), ("bookmark", 0.5), ("more_like", 0.5)],
)
def test_implicit_reinforce_strengths(signal, strength):
    out, _ = run(mem(A), [op("reinforce", "agent memory", "implicit", signal)])
    assert w(out, "a") == pytest.approx(0.5 + 0.15 * strength)


def test_implicit_confidence_step_and_cap():
    out, _ = run(mem(A), [op("reinforce", "agent memory", "implicit", "dive")])
    assert get(out, "a").confidence == pytest.approx(0.55)
    hi = A.model_copy(update={"confidence": 0.79})
    out, _ = run(mem(hi), [op("reinforce", "agent memory", "implicit", "dive")])
    assert get(out, "a").confidence == pytest.approx(0.8)


def test_implicit_add_creates_implicit_interest():
    out, _ = run(mem(), [op("add", "tactile sensing", "implicit", "dive", subtopic="robotics-hardware")])
    i = out.interests[0]
    assert i.source == "implicit" and i.weight == pytest.approx(0.075) and i.confidence == pytest.approx(0.2)


@pytest.mark.parametrize("o", ["pin", "unpin", "remove"])
def test_implicit_pin_unpin_remove_are_ignored(o):
    out, rep = run(mem(A), [op(o, "agent memory", "implicit", "dive")])
    assert out.interests == mem(A).interests
    assert rep.changes[-1].action == "ignored"


def test_implicit_reinforce_with_negative_signal_ignored():
    out, _ = run(mem(A), [op("reinforce", "agent memory", "implicit", "skip")])
    assert w(out, "a") == 0.5


def test_not_reached_never_changes_memory():
    out, rep = run(
        mem(A),
        [
            op("weaken", "agent memory", "explicit", "not_reached"),
            op("reinforce", "agent memory", "implicit", "not_reached"),
        ],
    )
    assert w(out, "a") == 0.5
    assert all(c.action == "ignored" for c in rep.changes)


# ---- exposure + blend ---------------------------------------------------------------------------


def test_exposure_heard_and_engaged_once_per_story():
    m = mem(A, interest("b", "open models", "open-models", 0.5))
    s = session(
        signals=[sig("dive", "s1"), sig("followup", "s1"), sig("save_for_later", "s2")],
        shown=[story("s1", ["a"]), story("s2", ["a", "b", "unknown"]), story("s3", ["b"])],
    )
    out, _ = run(m, s=s)
    assert (get(out, "a").shown_n, get(out, "a").engaged_n) == (2, 2)
    assert (get(out, "b").shown_n, get(out, "b").engaged_n) == (2, 1)


def test_not_reached_and_unheard_not_counted_as_exposure():
    s = session(
        signals=[sig("not_reached", "s1")], shown=[story("s1", ["a"]), story("s2", ["a"], heard=False)]
    )
    out, _ = run(mem(A), s=s)
    assert get(out, "a").shown_n == 0


def test_engaged_story_counts_as_heard():
    s = session(signals=[sig("dive", "s1")], shown=[story("s1", ["a"], heard=False)])
    out, _ = run(mem(A), s=s)
    assert (get(out, "a").shown_n, get(out, "a").engaged_n) == (1, 1)


def test_blend_for_implicit_sourced_touched_interests():
    imp = interest("a", "agent memory", "agents-and-tools", 0.5, source="implicit", shown_n=1, engaged_n=0)
    s = session(signals=[sig("dive", "s1")], shown=[story("s1", ["a"])])
    out, rep = run(mem(imp), [op("reinforce", "agent memory", "implicit", "dive")], s=s)
    e = (1 + 1) / (2 + 3)
    assert w(out, "a") == pytest.approx(0.7 * (0.5 + 0.075) + 0.3 * e)
    assert any(c.action == "blend" for c in rep.changes)


def test_no_blend_for_onboarding_or_untouched():
    onb = interest("a", "agent memory", "agents-and-tools", 0.5, source="onboarding")
    s = session(shown=[story("s1", ["a"])])
    assert w(run(mem(onb), s=s)[0], "a") == 0.5
    imp = interest("b", "x", "open-models", 0.5, source="implicit")
    assert w(run(mem(imp), s=session())[0], "b") == 0.5


# ---- skip rule ----------------------------------------------------------------------------------


def outcome(sid, days_ago, o=1.0, skipped=("a",)):
    return SessionOutcome(
        session_id=sid, at=NOW - timedelta(days=days_ago), opportunity=o, skipped_interest_ids=list(skipped)
    )


SKIP = op("weaken", "agent memory", "implicit", "skip")


def test_skip_single_session_not_enough():
    out, rep = run(mem(A), [SKIP], s=session(signals=[sig("skip", "s1")], shown=[story("s1", ["a"])]))
    assert w(out, "a") == 0.5
    assert any(c.action == "skip_rule_not_met" for c in rep.changes)
    assert rep.outcome.skipped_interest_ids == ["a"] and rep.outcome.opportunity == 1.0


def test_skip_two_of_last_four_full_sessions_weakens():
    out, _ = run(
        mem(A),
        [SKIP],
        s=session(signals=[sig("skip", "s1")], shown=[story("s1", ["a"])]),
        prior=[outcome("p1", 3)],
    )
    assert w(out, "a") == pytest.approx(0.5 - 0.15 * 0.2 * 1.0)


def test_skip_in_cut_short_sessions_do_not_count():
    out, _ = run(
        mem(A),
        [SKIP],
        s=session(signals=[sig("skip", "s1")], shown=[story("s1", ["a"])]),
        prior=[outcome("p1", 3, o=0.3), outcome("p2", 2, o=0.3)],
    )
    assert w(out, "a") == 0.5


def test_skip_outside_window_does_not_count():
    prior = [
        outcome("p0", 10),
        outcome("p1", 4, skipped=()),
        outcome("p2", 3, skipped=()),
        outcome("p3", 2, skipped=()),
    ]
    out, _ = run(
        mem(A), [SKIP], s=session(signals=[sig("skip", "s1")], shown=[story("s1", ["a"])]), prior=prior
    )
    assert w(out, "a") == 0.5  # window = current + p1..p3; p0 is the 5th most recent


def test_cut_short_current_session_scales_by_opportunity():
    prior = [outcome("p1", 3), outcome("p2", 2)]
    s = session(signals=[sig("skip", "s1")], shown=[story("s1", ["a"])], ctx=SHORT_CTX)
    out, rep = run(mem(A), [SKIP], s=s, prior=prior)
    assert rep.opportunity == 0.3
    assert w(out, "a") == pytest.approx(0.5 - 0.15 * 0.2 * 0.3)


def test_skip_rule_function_dedupes_sessions_and_counts_current():
    cur = outcome("cur", 0)
    assert skip_rule_met("a", cur, [outcome("p1", 1)], NOCAP)
    assert not skip_rule_met("a", cur, [cur], NOCAP)
    assert not skip_rule_met("a", outcome("cur", 0, o=0.3), [outcome("p1", 1)], NOCAP)


def test_a3_removes_skip_condition_and_opportunity():
    flags = ExperimentFlags(session_ctx_weighting=False)
    s = session(signals=[sig("skip", "s1")], shown=[story("s1", ["a"])], ctx=SHORT_CTX)
    out, rep = run(mem(A), [SKIP], s=s, flags=flags)
    assert rep.opportunity == 1.0
    assert w(out, "a") == pytest.approx(0.5 - 0.15 * 0.2)


def test_skip_op_marks_interest_skipped_in_outcome():
    out, rep = run(mem(A), [SKIP], s=session())
    assert rep.outcome.skipped_interest_ids == ["a"]


# ---- concentration guard ------------------------------------------------------------------------


def shares(interests):
    tot = sum(i.weight for i in interests)
    by: dict[str, float] = {}
    for i in interests:
        k = i.subtopic if i.subtopic != "other" else f"other:{i.label}"
        by[k] = by.get(k, 0) + i.weight
    return {k: v / tot for k, v in by.items()}


def test_guard_caps_dominant_subtopic_exactly():
    ints = [
        interest("a1", "x", "agents-and-tools", 2.0),
        interest("a2", "y", "agents-and-tools", 1.0),
        interest("b", "b", "open-models", 0.5),
        interest("c", "c", "humanoids", 0.5),
    ]
    out, ch = concentration_guard(ints, memory_config())
    assert shares(out)["agents-and-tools"] == pytest.approx(0.35)
    a1, a2 = out[0].weight, out[1].weight
    assert a1 / a2 == pytest.approx(2.0)  # proportions inside the group are kept
    assert out[2].weight == 0.5 and out[3].weight == 0.5
    assert {c.action for c in ch} == {"cap"}


def test_guard_water_fills_multiple_groups():
    ints = [
        interest("a", "a", "agents-and-tools", 10),
        interest("b", "b", "open-models", 10),
        interest("c", "c", "humanoids", 1),
        interest("d", "d", "solar-wind", 1),
    ]
    out, _ = concentration_guard(ints, memory_config())
    s = shares(out)
    assert max(s.values()) <= 0.35 + 1e-9
    assert s["agents-and-tools"] == pytest.approx(0.35) and s["open-models"] == pytest.approx(0.35)


def test_guard_exempts_pinned_group():
    ints = [
        interest("a", "a", "agents-and-tools", 5, explicit_pin=True),
        interest("b", "b", "open-models", 1),
        interest("c", "c", "humanoids", 1),
    ]
    out, ch = concentration_guard(ints, memory_config())
    assert out[0].weight == 5 and ch == []


def test_guard_groups_other_by_label():
    ints = [
        interest("a", "knitting", "other", 1),
        interest("b", "chess", "other", 1),
        interest("c", "c", "humanoids", 1),
    ]
    out, ch = concentration_guard(ints, memory_config())
    assert ch == [] and out == ints  # grouped together, "other" would hold 2/3 and be capped


def test_guard_infeasible_is_skipped():
    ints = [interest("a", "a", "agents-and-tools", 3), interest("b", "b", "open-models", 1)]
    out, ch = concentration_guard(ints, memory_config())
    assert out == ints and ch[0].action == "cap_skipped"


def test_guard_noop_when_balanced_or_empty():
    ints = [
        interest(str(n), str(n), s, 1) for n, s in enumerate(["agents-and-tools", "open-models", "humanoids"])
    ]
    assert concentration_guard(ints, memory_config()) == (ints, [])
    assert concentration_guard([], memory_config()) == ([], [])


def test_guard_runs_inside_apply():
    m = mem(
        interest("a", "a", "agents-and-tools", 3),
        interest("b", "b", "open-models", 1),
        interest("c", "c", "humanoids", 1),
    )
    out, _ = run(m, cfg=memory_config())
    assert shares(out.interests)["agents-and-tools"] == pytest.approx(0.35)


# ---- floor --------------------------------------------------------------------------------------


def test_floor_drops_old_low_weight_unpinned():
    old = NOW - timedelta(days=15)
    m = mem(
        interest("low", "low", "humanoids", 0.04, last_reinforced_at=old),
        interest("recent", "r", "solar-wind", 0.04, last_reinforced_at=NOW - timedelta(days=3)),
        interest("pinned", "p", "open-models", 0.01, last_reinforced_at=old, explicit_pin=True),
        interest("unknown", "u", "nuclear-fusion", 0.01, last_reinforced_at=None),
        interest("ok", "ok", "agents-and-tools", 0.05, last_reinforced_at=old),
    )
    out, rep = run(m)
    assert {i.id for i in out.interests} == {"recent", "pinned", "ok"}
    assert sum(1 for c in rep.changes if c.action == "floor_drop") == 2


# ---- delivery -----------------------------------------------------------------------------------


def test_delivery_changes():
    m = mem(
        A,
        delivery=[
            DeliveryPref(key="length_s", value=180, confidence=0.5),
            DeliveryPref(key="depth", value="standard", confidence=0.3),
        ],
    )
    out, _ = run(
        m,
        delivery=[
            DeliveryChange(key="length_s", value=120, explicit=True),
            DeliveryChange(key="depth", value="deep", explicit=False),
            DeliveryChange(key="technicality", value="high", explicit=False),
        ],
    )
    d = {p.key: p for p in out.delivery}
    assert d["length_s"].value == 120 and d["length_s"].confidence == pytest.approx(0.8)
    assert d["depth"].value == "deep" and d["depth"].confidence == pytest.approx(0.4)
    assert d["technicality"].value == "high"
    out2, rep = run(out, delivery=[DeliveryChange(key="length_s", value=300, explicit=False)])
    assert {p.key: p.value for p in out2.delivery}["length_s"] == 120
    assert rep.changes[-1].action == "delivery_kept"


# ---- modes, purity ------------------------------------------------------------------------------


def test_b2_static_freezes_memory_but_reports_outcome():
    m = mem(A)
    s = session(signals=[sig("skip", "s1")], shown=[story("s1", ["a"])])
    out, rep = run(
        m,
        [op("reinforce", "agent memory", "explicit", "more_like")],
        s=s,
        flags=ExperimentFlags(memory="static"),
    )
    assert out == m
    assert rep.mode == "static" and rep.outcome.skipped_interest_ids == ["a"]


def test_b5_naive_appends_explicit_statements_only():
    m = mem(A, profile_text="- I like AI agents")
    s = session(
        signals=[
            sig("more_like", "s1", explicit=True, turn_id="t2"),
            sig("dive", "s1", turn_id="t3"),
            sig("keep_posted", explicit=True, hint="fusion startups"),
        ],
        transcript=[
            TranscriptTurn(turn_id="t2", role="user", text="More stories like this please."),
            TranscriptTurn(turn_id="t3", role="user", text="Tell me more."),
        ],
    )
    out, rep = run(
        m,
        [op("remove", "agent memory", "explicit", "stop_covering")],
        s=s,
        flags=ExperimentFlags(pipeline="naive"),
    )
    assert out.interests == m.interests
    assert out.profile_text == (
        "- I like AI agents\n- More stories like this please.\n- keep posted: fusion startups"
    )
    assert rep.mode == "naive"


def test_apply_is_pure_and_deterministic():
    m = mem(A, interest("b", "open models", "open-models", 0.5, source="implicit"))
    before = m.model_copy(deep=True)
    ops = [
        op("reinforce", "agent memory", "implicit", "dive"),
        op("add", "robots", "explicit", "more_like", subtopic="humanoids"),
    ]
    s = session(signals=[sig("dive", "s1")], shown=[story("s1", ["a", "b"])])
    r1 = run(m, ops, s=s, cfg=memory_config())
    r2 = run(m, ops, s=s, cfg=memory_config())
    assert r1 == r2
    assert m == before


def test_evidence_capped():
    m = mem(A.model_copy(update={"evidence": [f"e{n}" for n in range(25)]}))
    out, _ = run(m, [op("reinforce", "agent memory", "explicit", "more_like", evidence=["new"])])
    ev = get(out, "a").evidence
    assert len(ev) == 20 and ev[-1] == "new"
