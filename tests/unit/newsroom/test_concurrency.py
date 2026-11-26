"""N5 with concurrent batches: same digest and trace order as the sequential loop."""

from __future__ import annotations

import asyncio
import random

from app.agents.newsroom import run_newsroom
from app.agents.newsroom.llm_steps import split_quota
from app.contracts.llm import ChatRequest, ChatResponse

from .fakes import FakeFetchTool, FakeModelClient
from .helpers import load_fixture, make_embedder, make_settings

OBS, RUNS = load_fixture()


class JitterClient(FakeModelClient):
    """Answers score calls after a random delay, so concurrent batches finish out of order."""

    def __init__(self, seed: int, **kw):
        super().__init__(**kw)
        self.rng = random.Random(seed)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        if req.role == "newsroom.score":
            await asyncio.sleep(self.rng.uniform(0, 0.02))
        return await super().chat(req)


async def run(concurrency: int, seed: int, **client_kw):
    client = JitterClient(seed, **client_kw)
    tools = {"fetch": FakeFetchTool()} if client_kw.get("fetch_urls") else {}
    digest, trace = await run_newsroom(
        "ai",
        RUNS[0],
        OBS,
        client,
        tools,
        None,
        embedder=make_embedder(),
        settings=make_settings(newsroom={"score_batch_size": 3}),
        score_concurrency=concurrency,
    )
    return digest, trace


def summary(trace):
    return {
        "llm": [
            (c.step, c.role, c.ok, c.error, c.output if c.output and "entries" in c.output else None)
            for c in trace.llm_calls
        ],
        "tools": [(t.tool, t.arguments, t.ok, t.error) for t in trace.tool_calls],
        "degraded": trace.degraded,
        "usage": (trace.usage.llm_calls, trace.usage.tokens_in, trace.usage.fetches),
        "n5": next(s.detail for s in trace.steps if s.step == "N5"),
    }


async def test_concurrent_batches_match_sequential():
    d1, t1 = await run(1, seed=1)
    assert next(s.detail for s in t1.steps if s.step == "N5")["batches"] > 2
    for conc, seed in [(3, 2), (8, 3)]:
        d, t = await run(conc, seed)
        assert d.model_dump() == d1.model_dump()
        assert summary(t) == summary(t1)


async def test_concurrent_fetch_quota_is_deterministic_and_within_budget():
    outs = []
    for seed in (4, 5, 6):
        d, t = await run(4, seed, fetch_urls=2)
        outs.append((d.model_dump(), summary(t)))
        assert t.usage.fetches <= 5  # budgets.newsroom_run.fetches
    assert outs[0] == outs[1] == outs[2]


def test_split_quota():
    assert split_quota(5, 4) == [2, 1, 1, 1]
    assert split_quota(0, 3) == [0, 0, 0]
    assert split_quota(7, 1) == [7]
    assert split_quota(3, 0) == []
