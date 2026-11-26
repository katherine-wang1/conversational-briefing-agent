from datetime import UTC, datetime

from app.agents.newsroom.editor import GAP_SOURCE_ID, EditorSettings, gap_search_observations
from app.contracts.sources import SearchHit
from app.contracts.tools import SearchResult, ToolResult

AT = datetime(2026, 11, 26, 12, tzinfo=UTC)


class FakeSearch:
    name = "search"

    def __init__(self, fail_on: str | None = None):
        self.calls = []
        self.fail_on = fail_on

    async def __call__(self, ctx, **kw):
        self.calls.append((ctx, kw))
        if kw["query"] == self.fail_on:
            return ToolResult(tool="search", ok=False, error="search_unavailable")
        hit = SearchHit(doc_id=kw["query"], url=f"https://ex.com/{len(self.calls)}", title=f"T {kw['query']}")
        return ToolResult(tool="search", ok=True, data=SearchResult(hits=[hit]))


async def test_gap_search_turns_hits_into_observations():
    tool = FakeSearch(fail_on="q2")
    es = EditorSettings(gap_search=True, gap_queries={"ai": ["q1", "q2", "q3"]}, gap_max_searches=2)
    obs, info = await gap_search_observations(
        "ai", "AI", AT, {"search": tool}, es, mode="replay", run_id="r", job_id=None, window_hours=24
    )
    assert [kw["query"] for _, kw in tool.calls] == ["q1", "q2"]  # capped
    assert all(ctx.mode == "replay" and ctx.now == AT and ctx.user_id is None for ctx, _ in tool.calls)
    assert len(obs) == 1 and obs[0].source_id == GAP_SOURCE_ID and obs[0].observed_at == AT
    assert info["hits"] == 1 and info["errors"]


async def test_gap_search_without_tool_is_a_noop():
    obs, info = await gap_search_observations(
        "ai", "AI", AT, {}, EditorSettings(gap_search=True), mode="live", run_id="r", job_id=None,
        window_hours=24,
    )
    assert obs == [] and info["errors"] == ["no search tool"]
