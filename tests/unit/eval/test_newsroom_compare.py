"""Newsroom comparison harness: packing a day for commit, and the per-day summary."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from app.contracts.sources import HeadlineObservation


@pytest.fixture
def nc(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSROOM_COMPARE_DIR", str(tmp_path))
    import eval.newsroom_compare.common as common

    importlib.reload(common)
    import eval.newsroom_compare.arms as arms
    import eval.newsroom_compare.pool as pool

    importlib.reload(arms)
    importlib.reload(pool)
    yield common, arms, pool
    monkeypatch.delenv("NEWSROOM_COMPARE_DIR")
    importlib.reload(common)
    importlib.reload(arms)
    importlib.reload(pool)


AT = datetime(2026, 11, 26, 20, tzinfo=UTC)


def test_pack_then_load_observations(nc):
    common, arms, _ = nc
    d = common.day_dir("2026-11-26", "ai")
    o = HeadlineObservation(source_id="s", beat="ai", observed_at=AT, url="https://e.com/a", title="A title")
    (d / "observations.jsonl").write_text(o.model_dump_json() + "\n")
    (d / "editor.trace.json").write_text("{}")
    arms.pack("2026-11-26", "ai")
    assert not (d / "observations.jsonl").exists() and (d / "observations.jsonl.gz").exists()
    assert (d / "editor.trace.json.gz").exists()
    assert [x.url for x in arms.load_observations("2026-11-26", "ai")] == ["https://e.com/a"]


def test_summary_overlap_unique_and_paywalled(nc):
    common, _, pool = nc
    d = common.day_dir("2026-11-26", "ai")

    def story(arm, rank, headline, url):  # noqa: ANN001, ANN202
        return common.CompareStory(
            arm=arm, rank=rank, headline=headline, urls=[url], outlets=[common.host(url)]
        )

    a = common.ArmResult(
        arm="editor",
        beat="ai",
        at=AT,
        wall_s=30,
        peak_rss_mb=100,
        cost_usd=0.008,
        stories=[
            story("editor", 1, "Nvidia halts server shipments", "https://www.wsj.com/n"),
            story("editor", 2, "SAP cuts 10,000 jobs", "https://ft.com/sap"),
        ],
    )
    b = common.ArmResult(
        arm="eventregistry",
        beat="ai",
        at=AT,
        wall_s=12,
        notes={"usage": {"tokens": 15.0}},
        stories=[
            story("eventregistry", 1, "Something else entirely", "https://bbc.com/x"),
            story("eventregistry", 2, "Nvidia halts server shipments", "https://www.wsj.com/n"),
        ],
    )
    common.write_json(d / "editor.json", a)
    common.write_json(d / "eventregistry.json", b)
    (d / "editor.trace.json").write_text("{}")  # traces are not arm files
    out = pool.summarize("2026-11-26", ["ai"])
    assert "| editor | 30.0 | 100.0 | 0.0080 | 2 | 2 | 2 (ft.com, wsj.com) | 1 |" in out
    assert "| eventregistry | 12.0 | None | 15 ER tokens | 2 | 2 | 1 (wsj.com) | 1 |" in out
    assert "| editor | 2 | 1 |" in out  # overlap row: 2 with itself, 1 shared
    assert "**editor**: SAP cuts 10,000 jobs (#2)" in out
