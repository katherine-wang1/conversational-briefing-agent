"""Live smoke tests against real providers. Skipped unless LLM_LIVE_TESTS=1 (they spend ~$0.001).

    LLM_LIVE_TESTS=1 pytest -m live tests/unit/llm/test_live.py
"""

import os

import pytest

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.llm import ChatRequest, Message
from app.llm.ledger import Ledger, MemoryLedgerSink
from app.llm.openrouter import OpenRouterClient
from app.llm.router import Router
from app.llm.tinker import TinkerClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LLM_LIVE_TESTS") != "1", reason="set LLM_LIVE_TESTS=1 to run"),
]


def _req() -> ChatRequest:
    return ChatRequest(role="live.smoke", messages=[Message(role="user", content="Reply with: ok")],
                       max_output_tokens=16, budget=TokenBudget(input_tokens=1000),
                       trace=TraceCtx(run_id="live-test"))


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
async def test_openrouter_dev_default_live():
    from app.config import loader

    m = loader.models().dev_default
    r = Router({"openrouter": OpenRouterClient()}, ledger=Ledger([MemoryLedgerSink()], spend_cap_usd=0.01))
    resp = await r.bind(m.provider, m.model, m.params).chat(_req())
    assert resp.content and resp.usage.cost_usd is not None


@pytest.mark.skipif(not os.environ.get("TINKER_API_KEY"), reason="TINKER_API_KEY not set")
async def test_tinker_inkling_small_live():
    r = Router({"tinker": TinkerClient()}, ledger=Ledger([MemoryLedgerSink()], spend_cap_usd=0.01))
    resp = await r.bind("tinker", "inkling-small", {"reasoning_effort": "none"}).chat(_req())
    assert resp.content and resp.usage.tokens_in > 0
