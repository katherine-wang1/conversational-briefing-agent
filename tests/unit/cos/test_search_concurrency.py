"""C2: plan queries run concurrently but are applied in plan order (same finds, trace and prompt)."""

from __future__ import annotations

import asyncio
import copy
import random

from app.agents.cos.llm import CallCtx
from app.agents.cos.search_loop import run_search_loop
from app.agents.cos.trace import RunTrace
from app.config import loader
from app.contracts.brief import SearchPlan, SearchQuery
from app.contracts.common import TokenBudget
from app.contracts.tools import ToolResult
from tests.unit.cos.fakes import FakeModelClient, FakeSearch, tc
from tests.unit.cos.fixtures import NOW, documents
from tests.unit.cos.test_pipeline import run as run_daily

QUERIES = ["memory agents", "reactor licensing", "chip export", "memory", "agents", "Hooli funding"]


class JitterSearch(FakeSearch):
    def __init__(self, *a, seed: int, fail_queries=(), **kw):
        super().__init__(*a, **kw)
        self.rng = random.Random(seed)
        self.fail_queries = set(fail_queries)

    async def __call__(self, ctx, **kw):
        await asyncio.sleep(self.rng.uniform(0, 0.01))
        if kw["query"] in self.fail_queries:
            self.calls.append(kw)
            return ToolResult(tool="search", ok=False, error="search unavailable")
        return await super().__call__(ctx, **kw)


async def run(concurrency: int, seed: int, *, max_searches=12, fail_queries=()):
    seen = []

    def loop(req):
        seen.append(req.messages[1].content)
        return [tc("finish")]

    c = CallCtx(
        client=FakeModelClient({"cos.search_loop": loop}),
        budget=TokenBudget(input_tokens=50_000, searches=12),
        trace=RunTrace(),
    )
    plan = SearchPlan(queries=[SearchQuery(query=q, kind="personal", rationale="r") for q in QUERIES])
    search = JitterSearch(documents(), seed=seed, fail_queries=fail_queries)
    finds, state = await run_search_loop(
        c,
        plan,
        {"search": search},
        mode="replay",
        now=NOW,
        max_searches=max_searches,
        concurrency=concurrency,
    )
    events = [(e.step, e.kind, e.ok, {k: v for k, v in e.detail.items()}) for e in c.trace.events]
    return [(f.hit.doc_id, f.query) for f in finds], state.used, state.exhausted, events, seen


async def test_plan_queries_concurrent_equals_sequential():
    base = await run(1, seed=0)
    assert base[0] and base[4]
    for conc, seed in [(3, 1), (6, 2)]:
        assert await run(conc, seed) == base


async def test_budget_and_failures_applied_in_plan_order():
    base = await run(1, seed=0, max_searches=4, fail_queries={"chip export", "memory"})
    assert base[1] == 4 and base[2]  # 2 failures in a row: exhausted
    assert await run(6, seed=3, max_searches=4, fail_queries={"chip export", "memory"}) == base


async def test_cos_daily_same_brief_and_packs_at_any_concurrency():
    outs = []
    for n in (1, 6):
        cfg = copy.deepcopy(loader.agent())
        cfg["concurrency"] = {"cos_search_queries": n, "cos_packs": n, "pack_fetches": n}
        script, packs, trace, client, _ = await run_daily(agent_cfg=cfg)
        events = [(e.step, e.kind, e.role, e.ok) for e in trace.events]
        outs.append((script.model_dump(), [p.model_dump() for p in packs], events, client.roles()))
    assert outs[0][:3] == outs[1][:3]
    assert sorted(outs[0][3]) == sorted(outs[1][3])  # same model calls (their order may interleave)
