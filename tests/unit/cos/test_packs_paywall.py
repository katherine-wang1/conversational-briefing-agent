"""C6 fallbacks for a picked digest story whose URLs give no text: free-coverage search, then publisher
summary passages (attributed, verbatim from the feed). Fakes only."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.agents.cos.config import PackDepthCfg, cos_config
from app.agents.cos.llm import CallCtx
from app.agents.cos.packs import build_pack
from app.agents.cos.pool import PoolItem
from app.agents.cos.trace import RunTrace
from app.agents.cos.write import PUBLISHER_SUMMARY_RULE, _payload
from app.contracts.brief import Candidate, SelectedStory
from app.contracts.common import TokenBudget
from app.contracts.digest import SourceSnippet
from app.contracts.sources import SearchHit
from app.contracts.tools import FetchResult, SearchResult, ToolContext, ToolResult, ToolSpec
from tests.unit.cos.fakes import FakeModelClient, extract_handler
from tests.unit.cos.fixtures import NOW, doc

BBG = "https://bloomberg.com/news/articles/sap-cuts"
FREE = "https://www.cnbc.com/2026/11/04/sap-job-cuts.html"
SNIP = SourceSnippet(
    url=BBG,
    outlet="Bloomberg",
    title="SAP Weighs Cutting at Least 10,000 Jobs",
    snippet="SAP SE is preparing a plan to eliminate at least 10,000 positions, people familiar said.",
)
FREE_TEXT = "SAP plans to cut 10,000 jobs in an AI restructuring, the company's staff were told. More detail."


class Tool:
    def __init__(self, name: str, fn: Any):
        self.name = name
        self.spec = ToolSpec(name=name, description="fake", parameters={"type": "object"})
        self.fn = fn
        self.calls: list[dict] = []

    async def __call__(self, ctx: ToolContext, **kw: Any) -> ToolResult:
        self.calls.append(kw)
        return self.fn(ctx, **kw)


def fetch_fn(ctx: ToolContext, **kw: Any) -> ToolResult:
    if kw["url"] == FREE:
        return ToolResult(tool="fetch", data=FetchResult(document=doc(FREE, "SAP cuts", FREE_TEXT)))
    return ToolResult(tool="fetch", ok=False, error="unavailable: HTTP 403")


def search_fn(hits: list[SearchHit]):
    def f(ctx: ToolContext, **kw: Any) -> ToolResult:
        ctx.budget.charge_search()
        return ToolResult(tool="search", data=SearchResult(hits=hits))

    return f


def item() -> PoolItem:
    return PoolItem(
        candidate=Candidate(
            story_ref="ai-sap",
            origin="digest",
            headline="SAP preparing to cut at least 10,000 jobs in AI restructuring",
            urls=[BBG],
        ),
        entities=["SAP"],
        source_snippets=[SNIP],
        first_seen=NOW - timedelta(hours=6),
    )


async def pack(tools: dict, *, searches: int | None = None):
    c = cos_config()
    cfg = c.model_copy(
        update={"packs": c.packs.model_copy(update={"light": PackDepthCfg(docs=1, tokens=800)})}
    )
    cc = CallCtx(
        client=FakeModelClient({"cos.extract_passages": extract_handler(1)}),
        budget=TokenBudget(input_tokens=50_000, searches=searches),
        trace=RunTrace(),
    )
    p = await build_pack(cc, item(), None, tools, mode="replay", now=NOW, cfg=cfg, history=[])
    return p, cc


async def test_c6_free_coverage_found_and_used_as_a_normal_document():
    hits = [
        SearchHit(
            doc_id="a",
            url="https://www.reuters.com/x",
            title="SAP to cut 10,000 jobs in AI restructuring",
            published_at=NOW - timedelta(hours=2),
        ),
        SearchHit(
            doc_id="b",
            url=FREE,
            title="SAP to cut at least 10,000 jobs in AI restructuring",
            published_at=NOW - timedelta(hours=2),
        ),
    ]
    fetch, search = Tool("fetch", fetch_fn), Tool("search", search_fn(hits))
    p, cc = await pack({"fetch": fetch, "search": search})
    assert [c["url"] for c in fetch.calls] == [BBG, FREE]  # reuters.com is paywalled: never fetched
    assert len(p.passages) == 1 and p.passages[0].url == FREE and p.passages[0].kind is None
    ev = [e for e in cc.trace.events if e.detail.get("found_via") == "free_coverage_search"]
    assert ev and ev[0].detail["url"] == FREE


async def test_c6_publisher_summary_passage_when_nothing_fetches():
    fetch, search = Tool("fetch", fetch_fn), Tool("search", search_fn([]))
    p, cc = await pack({"fetch": fetch, "search": search})
    assert len(search.calls) == 1
    assert len(p.passages) == 1
    ps = p.passages[0]
    assert ps.kind == "publisher_summary" and ps.source == "Bloomberg" and ps.url == BBG
    assert ps.text == f"{SNIP.title}\n{SNIP.snippet}"  # verbatim feed text only
    assert any("publisher summary" in str(e.detail) for e in cc.trace.degradations)
    # C7 sees the passage kind and the attribution rule for this story only
    sel = [SelectedStory(story_ref="ai-sap", slot="big_news", rank=1, why_tag="big news", mmr_score=1.0)]
    payload = _payload(sel, {"ai-sap": "SAP cuts"}, {"ai-sap": p}, [], [], None)
    assert 'kind="publisher_summary"' in payload and PUBLISHER_SUMMARY_RULE in payload


async def test_c6_search_budget_spent_skips_search_but_keeps_publisher_summary():
    fetch, search = Tool("fetch", fetch_fn), Tool("search", search_fn([]))
    p, _ = await pack({"fetch": fetch, "search": search}, searches=0)
    assert len(search.calls) == 1  # the tool refuses on the run's budget (BudgetExceeded): no hits
    assert [x.kind for x in p.passages] == ["publisher_summary"]


async def test_c6_without_search_tool_uses_publisher_summary():
    p, _ = await pack({"fetch": Tool("fetch", fetch_fn)})
    assert [x.kind for x in p.passages] == ["publisher_summary"]
