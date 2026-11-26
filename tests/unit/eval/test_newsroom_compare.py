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


def test_groups_ignore_urls_an_arm_cites_for_several_stories(nc):
    """A roundup page cited for several stories must not chain them into one group."""
    common, _, pool = nc
    roundup = "https://aiweekly.co/ai-news-today"

    def story(arm, rank, headline, urls):  # noqa: ANN001, ANN202
        return common.CompareStory(arm=arm, rank=rank, headline=headline, urls=urls, outlets=[])

    agent = common.ArmResult(
        arm="claude_agent",
        beat="ai",
        at=AT,
        stories=[
            story("claude_agent", 1, "Nvidia halts AI server shipments to four countries", [roundup, "https://t.co/n"]),
            story("claude_agent", 2, "S&P puts Nvidia rating on watch", [roundup, "https://rte.ie/sp"]),
            story("claude_agent", 3, "ASML warns on MATCH Act", [roundup]),
        ],
    )
    # editor and classic share a URL, so they form one group under the editor's wording; the agent's
    # headline only resembles classic's, so the group must be matched on all its members' headlines.
    editor = common.ArmResult(
        arm="editor",
        beat="ai",
        at=AT,
        stories=[story("editor", 1, "Southeast Asia deliveries paused by chipmaker", ["https://wsj.com/n"])],
    )
    classic = common.ArmResult(
        arm="classic",
        beat="ai",
        at=AT,
        stories=[story("classic", 1, "Nvidia halts AI server shipments to four countries", ["https://wsj.com/n"])],
    )
    groups = pool._groups([editor, classic, agent], 10)
    assert len(groups) == 3  # Nvidia, S&P, ASML: the roundup URL doesn't chain them
    assert sum(1 for g in groups if "claude_agent" in g["found_by"]) == 3
    assert groups[0]["found_by"] == {"editor": 1, "classic": 1, "claude_agent": 1}
