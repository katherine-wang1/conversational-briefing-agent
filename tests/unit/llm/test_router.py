import httpx
import pytest
from pydantic import BaseModel

from app.config.loader import ModelRole, ModelsCfg
from app.contracts.common import BudgetExceeded, TokenBudget
from app.contracts.tools import ToolSpec
from app.llm.cache import CallCache
from app.llm.errors import ProviderError, SpendCapExceeded, StructuredOutputError
from app.llm.ledger import JsonlLedgerSink, Ledger, MemoryLedgerSink
from app.llm.router import Router
from app.llm.tinker import TinkerClient, price_cost, resolve_model
from tests.unit.llm.helpers import Recorder, completion, make_openrouter, make_req, no_sleep, sse

CFG = ModelsCfg(
    dev_default=ModelRole(provider="openrouter", model="acme/json-model"),
    roles={
        "cos.score": ModelRole(provider="openrouter", model="TBD"),
        "cos.write_brief": ModelRole(provider="openrouter", model="acme/no-schema",
                                     params={"reasoning": {"effort": "low"}}),
        "judge.qa": ModelRole(provider="tinker", model="inkling-small"),
    },
)


class Score(BaseModel):
    relevance: float
    label: str


def router(rec: Recorder, *, cache: CallCache | None = None, cap: float | None = None,
           tinker_rec: Recorder | None = None) -> tuple[Router, MemoryLedgerSink]:
    sink = MemoryLedgerSink()
    providers = {"openrouter": make_openrouter(rec)}
    if tinker_rec is not None:
        providers["tinker"] = TinkerClient(api_key="tk", sleep=no_sleep,
                                           http=httpx.AsyncClient(transport=httpx.MockTransport(tinker_rec)))
    return Router(providers, ledger=Ledger([sink], spend_cap_usd=cap), cache=cache, models_cfg=CFG), sink


async def test_role_resolution_and_params():
    rec = Recorder([httpx.Response(200, json=completion("ok"))])
    r, sink = router(rec)
    await r.chat(make_req(role="cos.score"))  # TBD -> dev_default
    await r.chat(make_req(role="cos.write_brief"))
    assert rec.requests[0]["model"] == "acme/json-model"
    assert rec.requests[1]["model"] == "acme/no-schema"
    assert rec.requests[1]["reasoning"] == {"effort": "low"}
    assert r.resolve("judge.qa").provider == "tinker"


async def test_ledger_record_fields(tmp_path):
    rec = Recorder([httpx.Response(200, json=completion(
        "ok", usage={"prompt_tokens": 50, "completion_tokens": 4, "cost": 0.002,
                     "prompt_tokens_details": {"cached_tokens": 32}}))])
    r, sink = router(rec)
    jsonl = JsonlLedgerSink(tmp_path / "ledger.jsonl")
    r.ledger.sinks.append(jsonl)
    req = make_req(role="cos.score")
    req.trace.step = "C4"
    await r.chat(req)
    (row,) = sink.records
    assert (row.role, row.provider, row.model) == ("cos.score", "openrouter", "acme/json-model")
    assert (row.tokens_in, row.tokens_out, row.tokens_cached) == (50, 4, 32)
    assert row.cost_usd == pytest.approx(0.002) and row.cost_source == "provider"
    assert (row.run_id, row.job_id, row.step, row.kind, row.ok) == ("r1", "j1", "C4", "call", True)
    assert jsonl.read()[0] == row
    assert r.ledger.total_cost_usd == pytest.approx(0.002)


async def test_cost_estimated_when_provider_reports_none():
    rec = Recorder([httpx.Response(200, json=completion(
        "ok", usage={"prompt_tokens": 1000, "completion_tokens": 100}))])
    r, sink = router(rec)
    resp = await r.chat(make_req())
    assert resp.usage.cost_usd == pytest.approx(1000 * 1e-7 + 100 * 5e-7)
    assert sink.records[0].cost_source == "estimated"


async def test_structured_output_valid_first_try():
    rec = Recorder([httpx.Response(200, json=completion('```json\n{"relevance": 0.5, "label": "x"}\n```'))])
    r, sink = router(rec)
    resp = await r.chat(make_req(response_schema=Score))
    assert resp.parsed == {"relevance": 0.5, "label": "x"}
    assert len(sink.records) == 1


async def test_structured_output_one_repair_then_success():
    rec = Recorder([
        httpx.Response(200, json=completion('{"relevance": "high"}')),
        httpx.Response(200, json=completion('{"relevance": 0.9, "label": "core"}')),
    ])
    r, sink = router(rec)
    resp = await r.chat(make_req(response_schema=Score))
    assert resp.parsed == {"relevance": 0.9, "label": "core"}
    assert [x.kind for x in sink.records] == ["call", "repair"]
    repair_msgs = rec.requests[1]["messages"]
    assert repair_msgs[-2] == {"role": "assistant", "content": '{"relevance": "high"}'}
    assert "Return valid JSON matching this schema; errors:" in repair_msgs[-1]["content"]
    assert resp.usage.tokens_in == 20 and resp.usage.cost_usd == pytest.approx(0.002)


async def test_structured_output_fails_after_repair():
    rec = Recorder([httpx.Response(200, json=completion("not json at all"))])
    r, sink = router(rec)
    with pytest.raises(StructuredOutputError) as ei:
        await r.chat(make_req(response_schema=Score))
    assert len(rec.requests) == 2 and ei.value.raw == "not json at all"


async def test_structured_with_tool_calls_skips_validation():
    rec = Recorder([httpx.Response(200, json=completion(None, tool_calls=[
        {"id": "c", "type": "function", "function": {"name": "search", "arguments": "{}"}}]))])
    r, _ = router(rec)
    tool = ToolSpec(name="search", description="d", parameters={"type": "object", "properties": {}})
    resp = await r.chat(make_req(response_schema=Score, tools=[tool]))
    assert resp.parsed is None and resp.tool_calls[0].name == "search"


async def test_call_cache_hit_is_free_and_marked(tmp_path):
    cache = CallCache(tmp_path / "c.sqlite")
    rec = Recorder([httpx.Response(200, json=completion('{"relevance": 1, "label": "a"}'))])
    r, sink = router(rec, cache=cache)
    req1 = make_req(response_schema=Score, cache_namespace="replay/seed1")
    first = await r.chat(req1)
    req2 = make_req(response_schema=Score, cache_namespace="replay/seed1")
    second = await r.chat(req2)
    assert len(rec.requests) == 1
    assert not first.cached and second.cached
    assert second.usage.cost_usd == 0 and second.parsed == first.parsed
    assert [x.kind for x in sink.records] == ["call", "cache_hit"]
    assert sink.records[1].cost_usd == 0 and sink.records[1].cost_source == "cache"
    assert req2.budget.used_input_tokens == first.usage.tokens_in  # hits charge budget identically
    # different seed namespace -> independent sample
    await r.chat(make_req(response_schema=Score, cache_namespace="replay/seed2"))
    assert len(rec.requests) == 2


async def test_live_namespace_not_cached_by_default(tmp_path):
    cache = CallCache(tmp_path / "c.sqlite")
    rec = Recorder([httpx.Response(200, json=completion("ok"))])
    r, _ = router(rec, cache=cache)
    await r.chat(make_req())
    await r.chat(make_req())
    assert len(rec.requests) == 2 and len(cache) == 0


async def test_budget_precheck_blocks_before_http_and_charges_actual():
    rec = Recorder([httpx.Response(200, json=completion(
        "ok", usage={"prompt_tokens": 70, "completion_tokens": 30, "cost": 0}))])
    r, sink = router(rec)
    b = TokenBudget(input_tokens=100, output_tokens=40)
    await r.chat(make_req("x", budget=b))
    assert (b.used_input_tokens, b.used_output_tokens) == (70, 30)
    with pytest.raises(BudgetExceeded):
        await r.chat(make_req("y" * 400, budget=b))  # ~100+ estimated tokens
    assert len(rec.requests) == 1
    b2 = TokenBudget(input_tokens=10_000, output_tokens=30, used_output_tokens=30)
    with pytest.raises(BudgetExceeded):
        await r.chat(make_req(budget=b2))


async def test_spend_cap():
    rec = Recorder([httpx.Response(200, json=completion("ok"))])  # cost 0.001 each
    r, _ = router(rec, cap=0.0015)
    await r.chat(make_req())
    await r.chat(make_req())
    with pytest.raises(SpendCapExceeded):
        await r.chat(make_req())
    assert len(rec.requests) == 2


async def test_provider_error_is_ledgered():
    rec = Recorder([httpx.Response(400, json={"error": "bad"})])
    r, sink = router(rec)
    with pytest.raises(ProviderError):
        await r.chat(make_req())
    assert sink.records[0].ok is False and "400" in sink.records[0].error


async def test_router_stream_records_ttft_and_validates(tmp_path):
    body = sse([
        {"model": "acme/json-model", "choices": [{"index": 0, "delta": {"content": '{"relevance": 0.2,'}}]},
        {"model": "acme/json-model", "choices": [{"index": 0, "delta": {"content": ' "label": "a"}'},
                                                   "finish_reason": "stop"}]},
        {"model": "acme/json-model", "choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 6,
                                                             "cost": 0.0003}},
    ])
    rec = Recorder([httpx.Response(200, content=body)])
    cache = CallCache(tmp_path / "c.sqlite")
    r, sink = router(rec, cache=cache)
    b = TokenBudget(input_tokens=1000)
    events = [e async for e in r.stream(make_req(response_schema=Score, budget=b, cache_namespace="ns"))]
    assert events[-1].type == "done" and events[-1].response.parsed == {"relevance": 0.2, "label": "a"}
    row = sink.records[0]
    assert row.stream and row.ttft_ms is not None and row.cost_usd == pytest.approx(0.0003)
    assert b.used_input_tokens == 11
    # replay from cache as a stream
    events2 = [e async for e in r.stream(make_req(response_schema=Score, cache_namespace="ns"))]
    assert events2[0].type == "text_delta" and events2[-1].response.cached
    assert len(rec.requests) == 1


async def test_tinker_client_via_router():
    trec = Recorder([httpx.Response(200, json=completion(
        '{"relevance": 0.4, "label": "j"}', model="thinkingmachines/Inkling-Small:peft:262144:sampling-nvfp4",
        usage={"prompt_tokens": 1000, "completion_tokens": 100,
               "prompt_tokens_details": {"cached_tokens": 500}}))])
    r, sink = router(Recorder([httpx.Response(200, json=completion())]), tinker_rec=trec)
    resp = await r.chat(make_req(role="judge.qa", response_schema=Score))
    body = trec.requests[0]
    assert body["model"] == resolve_model("inkling-small")
    assert "response_format" not in body and "JSON schema" in body["messages"][0]["content"]
    assert resp.parsed == {"relevance": 0.4, "label": "j"}
    expected = (500 * 0.30 + 500 * 0.06 + 100 * 1.20) / 1e6
    assert resp.usage.cost_usd == pytest.approx(expected)
    assert sink.records[0].cost_source == "estimated" and sink.records[0].provider == "tinker"
    tool = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})
    with pytest.raises(ProviderError):
        await r.chat(make_req(role="judge.qa", tools=[tool]))


def test_price_cost_unknown_model():
    from app.contracts.llm import Usage

    assert price_cost("nope/model", Usage(tokens_in=5)) is None
