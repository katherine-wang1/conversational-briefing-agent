"""Labelling store: item builders, storage, blind payloads, accuracy and Cohen's kappa."""

from __future__ import annotations

import json

import pytest

from eval.labeling import store
from eval.labeling.build import main as build_main
from eval.sim.profile import panel_split

STORIES = [
    {
        "story_id": f"s{i}",
        "title": f"Synthetic story {i}",
        "summary": "x",
        "important_cov": i % 5 == 0,
        "subtopics": ["open-models"] if i % 2 else ["chips-and-compute"],
    }
    for i in range(200)
]


def test_kappa_known_values():
    # Classic 2x2 example: po = 0.7, pe = 0.5 -> kappa 0.4
    a2 = ["y"] * 50 + ["n"] * 50
    b2 = ["y"] * 35 + ["n"] * 15 + ["y"] * 15 + ["n"] * 35
    assert store.cohen_kappa(a2, b2) == pytest.approx(0.4)
    assert store.cohen_kappa(["a", "b"], ["a", "b"]) == 1.0
    assert store.cohen_kappa([], []) is None
    # quadratic weights penalise near-misses less
    h = ["1", "2", "3", "4", "5"] * 4
    j_near = ["2", "3", "4", "5", "4"] * 4
    j_far = ["5", "5", "1", "1", "1"] * 4
    opts = store.OPTIONS["relevance"]
    assert store.cohen_kappa(h, j_near, "quadratic", opts) > store.cohen_kappa(h, j_far, "quadratic", opts)


def test_agreement_report():
    human = {"i1": "supported", "i2": "unsupported", "i3": "supported", "i4": "partial"}
    judge = {"i1": "supported", "i2": "supported", "i3": "supported", "i4": "partial", "i9": "x"}
    a = store.agreement("claim", human, judge)
    assert a.n == 4 and a.accuracy == 0.75 and a.confusion["unsupported->supported"] == 1
    assert a.passes_bar is False
    r = store.agreement("relevance", {"a": "5", "b": "1"}, {"a": "4", "b": "1"})
    assert r.weighted_kappa is not None


def test_l1_builder_is_blind_stratified_and_deterministic():
    items = store.build_l1(STORIES, seed=1)
    topic = [i for i in items if i.kind == "topic"]
    imp = [i for i in items if i.kind == "importance"]
    assert len(topic) == 100 and len(imp) == 50
    assert sum(i.meta["stratum"] for i in imp) == 25
    for i in items:
        assert "subtopics" not in i.payload and "important_cov" not in i.payload
    assert [i.item_id for i in store.build_l1(STORIES, seed=1)] == [i.item_id for i in items]


def test_l2_and_l3_builders():
    items = store.build_l2(STORIES, panel_split("test") + panel_split("kate"), n=24)
    assert len(items) == 24 and len({i.meta["profile_id"] for i in items}) == 12  # placeholder kate excluded
    assert "Interests" in items[0].payload["profile"]
    l3 = store.build_l3(
        [{"item_id": "c1", "claim": "c", "sentence": "s", "passages": [["p1", "t"]]}],
        [{"item_id": "q1", "question": "q", "answer": "a", "passages": [], "answerability": "x"}],
    )
    assert [i.kind for i in l3] == ["claim", "qa"]


def test_storage_roundtrip_and_relabel(tmp_path):
    items = store.build_l1(STORIES[:10], n_topic=3, n_importance=2)
    ip, lp = store.session_paths("L1", tmp_path)
    store.write_items(items, ip)
    assert store.read_items(ip) == items
    labels = store.read_labels(lp)
    first = store.next_unlabelled(items, labels)
    store.append_label(
        store.LabelRecord(item_id=first.item_id, session="L1", kind=first.kind, label="no"), lp
    )
    store.append_label(
        store.LabelRecord(item_id=first.item_id, session="L1", kind=first.kind, label="yes"), lp
    )
    labels = store.read_labels(lp)
    assert labels[first.item_id].label == "yes"
    assert store.next_unlabelled(items, labels).item_id != first.item_id


def test_build_cli_writes_items_and_model_side_labels(tmp_path):
    src = tmp_path / "labels.jsonl"
    src.write_text("".join(json.dumps(s) + "\n" for s in STORIES))
    assert build_main(["L1", "--stories", str(src), "--out", str(tmp_path)]) == 0
    assert len(store.read_items(tmp_path / "items_L1.jsonl")) == 150
    jt = store.read_judge_labels(tmp_path / "judge_topic.jsonl")
    assert set(jt.values()) <= {"open-models", "chips-and-compute"}
    ji = store.read_judge_labels(tmp_path / "judge_importance.jsonl")
    assert list(ji.values()).count("yes") == 25


def test_streamlit_app_imports_without_streamlit():
    import eval.labeling.app as app  # streamlit is imported lazily inside main()

    assert callable(app.main)
