"""C6 packs: paywall-aware URL order, retrying further URLs, and concurrency that keeps order."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from app.agents.cos.config import PackDepthCfg, cos_config
from app.agents.cos.llm import CallCtx
from app.agents.cos.packs import build_pack, domain_in, order_urls, primary_domains
from app.agents.cos.pool import PoolItem
from app.agents.cos.trace import RunTrace
from app.contracts.brief import Candidate
from app.contracts.common import BudgetExceeded, TokenBudget
from app.contracts.sources import SearchHit
from app.contracts.tools import FetchResult, ToolContext, ToolResult, ToolSpec
from tests.unit.cos.fakes import FakeModelClient, extract_handler
from tests.unit.cos.fixtures import NOW, doc

PAYWALL = cos_config().packs.paywall_domains


def test_order_urls_puts_paywalls_last_and_primary_first_stably():
    urls = [
        "https://www.wsj.com/tech/a",
        "https://theverge.com/b",
        "https://asia.nikkei.com/c",
        "https://openai.com/index/d",
        "https://techcrunch.com/e",
        "https://ft.com/f",
    ]
    out = order_urls(urls, paywall=PAYWALL, primary=["openai.com"])
    assert out == [
        "https://openai.com/index/d",
        "https://theverge.com/b",
        "https://techcrunch.com/e",
        "https://www.wsj.com/tech/a",
        "https://asia.nikkei.com/c",
        "https://ft.com/f",
    ]
    assert order_urls(urls[1:2] + urls[4:5]) == urls[1:2] + urls[4:5]  # nothing to reorder: unchanged


def test_domain_matching_is_by_host_suffix_not_substring():
    assert domain_in("asia.nikkei.com", ["asia.nikkei.com"])
    assert domain_in("markets.ft.com", ["ft.com"])
    assert not domain_in("microsoft.com", ["ft.com"])
    assert not domain_in("nikkei.com", ["asia.nikkei.com"])


def test_paywall_list_and_primary_domains_come_from_config():
    assert {"wsj.com", "ft.com", "bloomberg.com", "asia.nikkei.com"} <= set(PAYWALL)
    assert "arxiv.org" in primary_domains()


class SlowFetch:
    """Fetch tool with random latency; some URLs fail (paywall) or come back empty."""

    name = "fetch"
    spec = ToolSpec(name="fetch", description="fake", parameters={"type": "object"})

    def __init__(self, docs, *, fail=(), empty=(), seed=0, budget_after: int | None = None):
        self.by_url = {d.url: d for d in docs}
        self.fail, self.empty = set(fail), set(empty)
        self.rng = random.Random(seed)
        self.calls: list[str] = []
        self.budget_after = budget_after

    async def __call__(self, ctx: ToolContext, **kw: Any) -> ToolResult:
        url = kw["url"]
        self.calls.append(url)
        if self.budget_after is not None and len(self.calls) > self.budget_after:
            raise BudgetExceeded("fetch budget")
        await asyncio.sleep(self.rng.uniform(0, 0.01))
        if url in self.fail:
            return ToolResult(tool="fetch", ok=False, error="unavailable: HTTP 401")
        d = self.by_url[url]
        if url in self.empty:
            d = d.model_copy(update={"text": "   "})
        return ToolResult(tool="fetch", data=FetchResult(document=d))


URLS = [f"https://site{n}.example/story" for n in range(6)]
DOCS = [doc(u, f"Story {n}", f"Sentence {n} about the event. More detail {n}.") for n, u in enumerate(URLS)]


def cfg_with(docs: int):
    c = cos_config()
    return c.model_copy(
        update={"packs": c.packs.model_copy(update={"light": PackDepthCfg(docs=docs, tokens=800)})}
    )


def item(urls, snippets=None) -> PoolItem:
    return PoolItem(
        candidate=Candidate(story_ref="s1", origin="digest", headline="The event", urls=list(urls)),
        snippets=snippets or {},
    )


async def pack(urls, fetch, *, docs=2, concurrency=None, snippets=None):
    c = CallCtx(
        client=FakeModelClient({"cos.extract_passages": extract_handler(1)}),
        budget=TokenBudget(input_tokens=50_000),
        trace=RunTrace(),
    )
    p = await build_pack(
        c,
        item(urls, snippets),
        None,
        {"fetch": fetch},
        mode="replay",
        now=NOW,
        cfg=cfg_with(docs),
        history=[],
        concurrency=concurrency,
    )
    return p, c


async def test_failed_and_empty_fetches_move_on_to_further_urls():
    fetch = SlowFetch(DOCS, fail={URLS[0]}, empty={URLS[1]})
    p, _ = await pack(URLS, fetch, docs=2)
    assert [ps.url for ps in p.passages] == [URLS[2], URLS[3]]
    assert fetch.calls[:2] == URLS[:2] and set(fetch.calls) == set(URLS[:4])


async def test_paywalled_url_is_tried_last():
    urls = ["https://www.wsj.com/a", URLS[0], URLS[1]]
    docs = [doc(urls[0], "WSJ", "Paywalled text. More."), *DOCS]
    fetch = SlowFetch(docs, fail={urls[0]})
    p, _ = await pack(urls, fetch, docs=2)
    assert fetch.calls == [URLS[0], URLS[1]]  # never needed the paywalled one
    assert [ps.url for ps in p.passages] == [URLS[0], URLS[1]]


async def test_snippets_only_fill_the_shortfall():
    hits = {
        u: SearchHit(doc_id=f"d{n}", url=u, title="t", source="s", snippet=f"snippet {n}")
        for n, u in enumerate(URLS[:3])
    }
    fetch = SlowFetch(DOCS, fail=set(URLS[:3]))
    p, c = await pack(URLS[:3], fetch, docs=2, snippets=hits)
    assert [ps.text for ps in p.passages] == ["snippet 0", "snippet 1"]
    assert len([e for e in c.trace.degradations if "snippet" in e.detail["reason"]]) == 2


async def test_attempts_are_capped_and_budget_stops_retries():
    many = [f"https://m{n}.example/x" for n in range(20)]
    docs = [doc(u, "t", "Text. More.") for u in many]
    fetch = SlowFetch(docs, fail=set(many))
    await pack(many, fetch, docs=1)
    assert len(fetch.calls) == cos_config().packs.max_fetch_attempts
    fetch = SlowFetch(docs, fail=set(many), budget_after=2)
    _, c = await pack(many, fetch, docs=1)
    assert len(fetch.calls) == 3 and any("budget" in e.detail["reason"] for e in c.trace.degradations)


async def test_concurrent_pack_equals_sequential_pack():
    fail = {URLS[1], URLS[4]}
    outs = []
    for seed, conc in [(1, 1), (2, 3), (3, 6)]:
        p, c = await pack(URLS, SlowFetch(DOCS, fail=fail, seed=seed), docs=3, concurrency=conc)
        events = [(e.step, e.kind, e.detail.get("url"), e.ok) for e in c.trace.events if e.kind != "llm"]
        outs.append((p.model_dump(), events))
    assert outs[0] == outs[1] == outs[2]
    assert [ps["url"] for ps in outs[0][0]["passages"]] == [URLS[0], URLS[2], URLS[3]]
