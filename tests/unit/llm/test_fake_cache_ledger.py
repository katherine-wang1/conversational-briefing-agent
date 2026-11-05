import pytest
from pydantic import BaseModel

from app.contracts.common import BudgetExceeded, TokenBudget
from app.contracts.llm import Message
from app.contracts.tools import ToolSpec
from app.llm.cache import CallCache, cache_key
from app.llm.errors import StructuredOutputError
from app.llm.fake import FakeModelClient, FakeReply
from app.llm.ledger import Ledger, MemoryLedgerSink
from tests.unit.llm.helpers import make_req


class Plan(BaseModel):
    queries: list[str]


async def test_fake_scripted_text_tools_structured():
    fake = FakeModelClient()
    fake.script("cos.search_loop", tool_calls=[("search", {"query": "x"})], sticky=False)
    fake.script("cos.search_loop", text="done")
    fake.script("cos.plan", structured={"queries": ["a", "b"]})
    r1 = await fake.chat(make_req(role="cos.search_loop"))
    assert r1.tool_calls[0].name == "search" and r1.tool_calls[0].arguments == {"query": "x"}
    r2 = await fake.chat(make_req(role="cos.search_loop"))
    r3 = await fake.chat(make_req(role="cos.search_loop"))  # last reply is sticky
    assert r2.content == r3.content == "done"
    r4 = await fake.chat(make_req(role="cos.plan", response_schema=Plan))
    assert r4.parsed == {"queries": ["a", "b"]}
    assert len(fake.calls_for("cos.search_loop")) == 3


async def test_fake_invalid_structured_raises_and_default():
    fake = FakeModelClient()
    fake.script("cos.plan", structured={"wrong": 1})
    with pytest.raises(StructuredOutputError):
        await fake.chat(make_req(role="cos.plan", response_schema=Plan))
    fake.default = None
    with pytest.raises(AssertionError):
        await fake.chat(make_req(role="unscripted"))


async def test_fake_responder_and_exception():
    fake = FakeModelClient()
    fake.responder = lambda req: FakeReply(text=req.messages[-1].content.upper())
    assert (await fake.chat(make_req("abc"))).content == "ABC"
    fake.responder = None
    fake.script("voice.turn", raise_exc=TimeoutError("boom"))
    with pytest.raises(TimeoutError):
        await fake.chat(make_req(role="voice.turn"))


async def test_fake_streaming_and_ledger_and_budget():
    sink = MemoryLedgerSink()
    fake = FakeModelClient(ledger=Ledger([sink]))
    fake.script("voice.turn", text="Hello there, friend.", chunks=3,
                tool_calls=[("open_story", {"story_ref": "s1"})])
    events = [e async for e in fake.stream(make_req(role="voice.turn"))]
    deltas = [e.text for e in events if e.type == "text_delta"]
    assert len(deltas) == 3 and "".join(deltas) == "Hello there, friend."
    assert [e.type for e in events][-2:] == ["tool_call", "done"]
    assert sink.records[0].stream and sink.records[0].role == "voice.turn"
    b = TokenBudget(input_tokens=5)
    with pytest.raises(BudgetExceeded):
        await fake.chat(make_req("x" * 100, budget=b))


def test_cache_key_sensitivity():
    tool = ToolSpec(name="t", description="d", parameters={})
    base = make_req("hi")
    k = cache_key("openrouter", "m", base)
    assert k == cache_key("openrouter", "m", make_req("hi"))
    assert k != cache_key("openrouter", "m2", base)
    assert k != cache_key("tinker", "m", base)
    assert k != cache_key("openrouter", "m", make_req("hi", cache_namespace="ns"))
    assert k != cache_key("openrouter", "m", make_req("hi", tools=[tool]))
    assert k != cache_key("openrouter", "m", make_req("hi", response_schema=Plan))
    assert k != cache_key("openrouter", "m", make_req("hi", temperature=0.5))
    assert k != cache_key("openrouter", "m", base, {"reasoning": {"effort": "low"}})
    other = make_req("hi")
    other.messages.append(Message(role="assistant", content="x"))
    assert k != cache_key("openrouter", "m", other)


def test_cache_roundtrip_memory():
    from app.contracts.llm import ChatResponse, Usage

    c = CallCache(":memory:")
    resp = ChatResponse(model="m", provider="p", content="x", usage=Usage(tokens_in=3, cost_usd=0.5),
                        latency_ms=99)
    c.put("k", "ns", resp)
    hit = c.get("k")
    assert hit.cached and hit.usage.cost_usd == 0 and hit.latency_ms == 0 and hit.content == "x"
    assert c.get("missing") is None
