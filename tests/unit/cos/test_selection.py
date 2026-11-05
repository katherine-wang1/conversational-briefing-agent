"""C5 selection: pure deterministic code (TDD §8.3)."""

import random

import pytest

from app.agents.cos.config import SelectionConfig, SlotCounts, cos_config
from app.agents.cos.selection import (
    ScoredItem,
    allocate_slots,
    for_you_score,
    k_from_length,
    resolve_k,
    select,
    select_importance_only,
)


def it(ref, imp=50, rel=0.0, nov=1.0, adj="core", beat="ai", origin="digest", feats=None, update_of=None):
    return ScoredItem(
        story_ref=ref,
        importance=imp,
        relevance=rel,
        novelty=nov,
        adjacency=adj,
        beat=beat,
        origin=origin,
        features=frozenset(feats if feats is not None else {ref}),
        update_of=update_of,
    )


def cfg(**kw) -> SelectionConfig:
    return SelectionConfig(**kw)


def slots_of(res):
    return {s.story_ref: s.slot for s in res.selected}


# ---- slot sizing --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "k,expected",
    [
        (6, {"big_news": 2, "for_you": 3, "outside_usual": 1}),
        (8, {"big_news": 2, "for_you": 5, "outside_usual": 1}),
        (5, {"big_news": 2, "for_you": 2, "outside_usual": 1}),
        (4, {"big_news": 2, "for_you": 1, "outside_usual": 1}),
        (3, {"big_news": 1, "for_you": 1, "outside_usual": 1}),
        (2, {"big_news": 1, "for_you": 1, "outside_usual": 0}),
        (1, {"big_news": 1, "for_you": 0, "outside_usual": 0}),
    ],
)
def test_allocate_slots_default(k, expected):
    out = allocate_slots(k, SlotCounts())
    assert out == expected
    assert sum(out.values()) == k


def test_allocate_slots_b3_no_outside():
    assert allocate_slots(6, SlotCounts(big_news=2, for_you=4, outside_usual=0)) == {
        "big_news": 2,
        "for_you": 4,
        "outside_usual": 0,
    }
    assert allocate_slots(3, SlotCounts(big_news=2, for_you=4, outside_usual=0))["outside_usual"] == 0


def test_resolve_k_and_length():
    c = cfg()
    assert resolve_k(None, c) == 6
    assert resolve_k(1, c) == 3
    assert resolve_k(20, c) == 8
    assert k_from_length(180, c) == 6
    assert k_from_length(60, c) == 3
    assert k_from_length(600, c) == 8
    assert k_from_length(None, c) == 6


def test_experiment_overrides_reach_selection_config():
    assert cos_config("b3_no_exploration").selection.slots.outside_usual == 0
    assert cos_config("b3_no_exploration").selection.slots.for_you == 4
    assert cos_config("full").selection.slots.outside_usual == 1
    assert cos_config("b1_importance_only").flags.personalization is False


# ---- slots -------------------------------------------------------------------------------------


def test_big_news_only_from_followed_beats():
    items = [it("en", 99, beat="energy"), it("ai1", 70), it("ai2", 60), it("ai3", 10, rel=0.1)]
    res = select(items, cfg(), k=3, followed_beats=["ai"])
    big = [s.story_ref for s in res.selected if s.slot == "big_news"]
    assert big == ["ai1"]
    assert slots_of(res).get("en") != "big_news"


def test_big_news_any_beat_when_none_followed():
    items = [it("en", 99, beat="energy"), it("ai1", 70)]
    res = select(items, cfg(slots=SlotCounts(big_news=1, for_you=0, outside_usual=0)), k=3, followed_beats=[])
    assert res.selected[0].story_ref == "en" and res.selected[0].slot == "big_news"


def test_per_user_items_without_beat_are_not_big_news():
    items = [it("u:1", 99, beat=None, origin="personal"), it("ai1", 40)]
    res = select(items, cfg(slots=SlotCounts(big_news=1, for_you=1, outside_usual=0)), k=3)
    assert slots_of(res)["ai1"] == "big_news"
    assert slots_of(res)["u:1"] == "for_you"


def test_for_you_uses_relevance_times_importance():
    a = it("a", imp=96, rel=0.5)  # 0.5 * 0.98 = 0.49
    b = it("b", imp=0, rel=0.9)  # 0.9 * 0.5 = 0.45
    assert for_you_score(a) == pytest.approx(0.49)
    assert for_you_score(b) == pytest.approx(0.45)
    res = select([a, b, it("big", 100)], cfg(slots=SlotCounts(big_news=1, for_you=1, outside_usual=0)), k=3)
    order = [s.story_ref for s in res.selected if s.slot == "for_you"]
    assert order[0] == "a"


def test_outside_usual_prefers_adjacent_without_floor():
    items = [
        it("big", 90),
        it("fy", 50, rel=0.9),
        it("adj", 10, adj="adjacent"),
        it("unrel", 95, adj="unrelated", beat="energy"),
    ]
    res = select(
        items, cfg(slots=SlotCounts(big_news=1, for_you=1, outside_usual=1)), k=3, followed_beats=["ai"]
    )
    assert slots_of(res)["adj"] == "outside_usual"
    assert "unrel" not in slots_of(res)


def test_outside_usual_unrelated_needs_floor():
    items = [
        it("big", 90),
        it("fy", 50, rel=0.9),
        it("low", 49, adj="unrelated"),
        it("high", 50, adj="unrelated", nov=0.5),
    ]
    res = select(items, cfg(slots=SlotCounts(big_news=1, for_you=1, outside_usual=1)), k=3)
    assert slots_of(res).get("high") == "outside_usual"
    assert "low" not in slots_of(res)


def test_outside_usual_never_core_and_unfilled_slot_goes_to_for_you():
    items = [it("big", 90), it("fy", 50, rel=0.9), it("core2", 30, rel=0.2), it("lowun", 10, adj="unrelated")]
    res = select(items, cfg(slots=SlotCounts(big_news=1, for_you=1, outside_usual=1)), k=3)
    s = slots_of(res)
    assert "outside_usual" not in s.values()
    assert s["core2"] == "for_you"  # filler from next best overall
    assert any("outside_usual short" in n for n in res.notes)


def test_outside_usual_ranked_by_importance_times_novelty():
    items = [
        it("big", 90),
        it("fy", 50, rel=0.9),
        it("a1", 80, adj="adjacent", nov=0.2),
        it("a2", 50, adj="adjacent", nov=0.9),
    ]
    res = select(items, cfg(slots=SlotCounts(big_news=1, for_you=1, outside_usual=1)), k=3)
    assert slots_of(res)["a2"] == "outside_usual"


def test_b3_overrides_produce_no_outside_slot():
    c = cos_config("b3_no_exploration").selection
    items = [it(f"s{i}", 50 + i, rel=0.1 * i) for i in range(8)] + [it("adj", 90, adj="adjacent")]
    res = select(items, c, k=6)
    assert "outside_usual" not in slots_of(res).values()
    assert len(res.selected) == 6


# ---- MMR ---------------------------------------------------------------------------------------


def test_mmr_penalises_redundant_big_news():
    dup = {"acme", "x-1", "model"}
    items = [it("a", 90, feats=dup), it("b", 89, feats=dup), it("c", 70, feats={"eu", "policy"})]
    c = cfg(slots=SlotCounts(big_news=2, for_you=0, outside_usual=0), mmr_lambda=0.7)
    res = select(items, c, k=3)
    big = [s.story_ref for s in res.selected if s.slot == "big_news"]
    assert big == ["a", "c"]


def test_mmr_lambda_one_ignores_redundancy():
    dup = {"acme", "x-1", "model"}
    items = [it("a", 90, feats=dup), it("b", 89, feats=dup), it("c", 70, feats={"eu"})]
    res = select(items, cfg(slots=SlotCounts(big_news=2, for_you=0, outside_usual=0), mmr_lambda=1.0), k=3)
    assert [s.story_ref for s in res.selected if s.slot == "big_news"] == ["a", "b"]


def test_custom_similarity_is_used():
    items = [it("a", 90), it("b", 89), it("c", 70)]
    res = select(
        items,
        cfg(slots=SlotCounts(big_news=2, for_you=0, outside_usual=0)),
        k=3,
        similarity=lambda x, y: 1.0 if {x.story_ref, y.story_ref} == {"a", "b"} else 0.0,
    )
    assert [s.story_ref for s in res.selected if s.slot == "big_news"] == ["a", "c"]


# ---- caps and constraints ---------------------------------------------------------------------


def test_carryover_max_one():
    items = [
        it("big", 90),
        it("c1", 50, rel=1.0, origin="carryover"),
        it("c2", 50, rel=0.99, origin="carryover"),
        it("x", 50, rel=0.2),
        it("y", 50, rel=0.1),
    ]
    res = select(items, cfg(slots=SlotCounts(big_news=1, for_you=3, outside_usual=0)), k=4)
    refs = [s.story_ref for s in res.selected]
    assert "c1" in refs and "c2" not in refs


def test_carryover_max_zero_excludes_carryovers():
    items = [
        it("big", 90),
        it("c1", 50, rel=1.0, origin="carryover"),
        it("x", 50, rel=0.2),
        it("y", 40, rel=0.1),
    ]
    res = select(items, cfg(carryover_max=0, slots=SlotCounts(big_news=1, for_you=2, outside_usual=0)), k=3)
    assert "c1" not in [s.story_ref for s in res.selected]


def test_min_beat_share_swaps_in_beat_stories():
    items = [it(f"en{i}", 90 - i, rel=0.9, beat="energy") for i in range(6)] + [
        it(f"ai{i}", 30 - i, rel=0.1) for i in range(5)
    ]
    c = cfg(min_beat_share={"ai": 0.67}, slots=SlotCounts(big_news=2, for_you=4, outside_usual=0))
    res = select(items, c, k=6, followed_beats=["ai", "energy"])
    beats = [s.story_ref[:2] for s in res.selected]
    assert beats.count("ai") >= 4
    assert len(res.selected) == 6
    assert any("min_beat_share[ai]" in n for n in res.notes)


def test_min_beat_share_infeasible_is_noted():
    items = [it(f"en{i}", 90 - i, rel=0.9, beat="energy") for i in range(6)] + [it("ai0", 30, rel=0.1)]
    c = cfg(min_beat_share={"ai": 0.67}, slots=SlotCounts(big_news=2, for_you=4, outside_usual=0))
    res = select(items, c, k=6, followed_beats=["ai", "energy"])
    assert [s.story_ref for s in res.selected].count("ai0") == 1
    assert any("infeasible" in n for n in res.notes)


def test_min_beat_share_respects_other_constrained_beat():
    items = [it(f"en{i}", 90 - i, rel=0.9, beat="energy") for i in range(6)] + [
        it(f"ai{i}", 30 - i, rel=0.1) for i in range(6)
    ]
    c = cfg(
        min_beat_share={"ai": 0.5, "energy": 0.5}, slots=SlotCounts(big_news=2, for_you=4, outside_usual=0)
    )
    res = select(items, c, k=6, followed_beats=["ai", "energy"])
    beats = [s.story_ref[:2] for s in res.selected]
    assert beats.count("ai") >= 3 and beats.count("en") >= 3


def test_min_beat_share_already_met_changes_nothing():
    items = [it(f"ai{i}", 90 - i, rel=0.5) for i in range(8)]
    a = select(items, cfg(), k=6)
    b = select(items, cfg(min_beat_share={"ai": 0.67}), k=6)
    assert a.selected == b.selected


# ---- order, determinism, edge cases -------------------------------------------------------------


def test_listening_order_big_updates_foryou_outside():
    items = [
        it("big", 95),
        it("upd", 60, rel=0.8, origin="update", update_of="old"),
        it("fy", 50, rel=0.9),
        it("adj", 40, adj="adjacent"),
    ]
    res = select(items, cfg(slots=SlotCounts(big_news=1, for_you=2, outside_usual=1)), k=4)
    assert [s.story_ref for s in res.selected] == ["big", "upd", "fy", "adj"]
    assert [s.rank for s in res.selected] == [1, 2, 3, 4]
    assert [s.why_tag for s in res.selected] == ["big news", "for you", "for you", "outside your usual"]


def test_deterministic_under_input_order_and_ties():
    items = [it(f"s{i:02d}", 50, rel=0.5) for i in range(12)] + [it("adj", 50, adj="adjacent")]
    base = select(items, cfg(), k=6)
    for seed in range(5):
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)
        assert select(shuffled, cfg(), k=6).selected == base.selected
    # ties break on ascending story_ref
    assert [s.story_ref for s in base.selected if s.slot == "big_news"] == ["adj", "s00"]


def test_pool_smaller_than_k():
    res = select([it("a", 90), it("b", 10, rel=0.5)], cfg(), k=6)
    assert len(res.selected) == 2
    assert any("pool exhausted" in n for n in res.notes)


def test_empty_pool():
    res = select([], cfg(), k=6)
    assert res.selected == []


def test_duplicate_refs_deduped():
    res = select([it("a", 90), it("a", 90), it("b", 80)], cfg(), k=3)
    assert sorted(res.refs) == ["a", "b"]


def test_no_story_selected_twice():
    items = [
        it(f"s{i}", 100 - i, rel=1 - i / 20, adj="adjacent" if i % 3 == 0 else "core") for i in range(15)
    ]
    res = select(items, cfg(), k=8)
    assert len(res.refs) == len(set(res.refs)) == 8


# ---- B1 importance only -------------------------------------------------------------------------


def test_b1_importance_only():
    items = [it("a", 90, rel=0.0), it("b", 20, rel=1.0), it("c", 80), it("d", 70, beat="energy")]
    res = select_importance_only(items, cfg(), k=3)
    assert [s.story_ref for s in res.selected] == ["a", "c", "d"]
    assert {s.slot for s in res.selected} == {"big_news"}


def test_b1_importance_only_respects_beat_share():
    items = [it(f"en{i}", 90 - i, beat="energy") for i in range(5)] + [it(f"ai{i}", 40 - i) for i in range(5)]
    res = select_importance_only(items, cfg(min_beat_share={"ai": 0.67}), k=3)
    assert sum(1 for s in res.selected if s.story_ref.startswith("ai")) >= 2
