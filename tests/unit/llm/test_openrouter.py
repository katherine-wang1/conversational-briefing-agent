import httpx
import pytest
from pydantic import BaseModel

from app.contracts.llm import Message, ToolCall
from app.contracts.tools import ToolSpec
from app.llm.errors import ProviderError, RetryableProviderError
from app.llm.openai_compat import INVALID_ARGS_KEY
from tests.unit.llm.helpers import Recorder, completion, make_openrouter, make_req, sse

TOOL = ToolSpec(name="search", description="search news",
                parameters={"type": "object", "properties": {"query": {"type": "string"}},
                            "required": ["query"]})


class Out(BaseModel):
    label: str
    score: float


async def test_chat_payload_usage_and_cost():
    rec = Recorder([httpx.Response(200, json=completion(
        "hello", usage={"prompt_tokens": 120, "completion_tokens": 7, "cost": 0.00042,
                        "prompt_tokens_details": {"cached_tokens": 100}}))])
    c = make_openrouter(rec)
    r = await c.complete(make_req("hi", max_output_tokens=50, temperature=0.2), "acme/json-model",
                         {"reasoning": {"effort": "low"}})
    body = rec.requests[0]
    assert body["model"] == "acme/json-model"
    assert body["max_tokens"] == 50 and body["temperature"] == 0.2
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["usage"] == {"include": True}
    assert body["reasoning"] == {"effort": "low"}  # params from config pass through
    assert rec.headers[0]["authorization"] == "Bearer test-key"
    assert r.content == "hello" and r.provider == "openrouter"
    assert (r.usage.tokens_in, r.usage.tokens_out, r.usage.tokens_cached) == (120, 7, 100)
    assert r.usage.cost_usd == pytest.approx(0.00042)


async def test_tool_calls_roundtrip():
    rec = Recorder([httpx.Response(200, json=completion(None, tool_calls=[
        {"id": "call_a", "type": "function", "function": {"name": "search", "arguments": '{"query": "x"}'}},
        {"id": "call_b", "type": "function", "function": {"name": "search", "arguments": "{not json"}},
    ], finish="tool_calls"))])
    c = make_openrouter(rec)
    req = make_req("find x", tools=[TOOL])
    req.messages += [
        Message(role="assistant", tool_calls=[ToolCall(id="c0", name="search", arguments={"query": "y"})]),
        Message(role="tool", tool_call_id="c0", content="<untrusted_source>...</untrusted_source>"),
    ]
    r = await c.complete(req, "acme/json-model", {})
    body = rec.requests[0]
    assert body["tools"][0] == {"type": "function", "function": TOOL.model_dump()}
    asst = body["messages"][1]
    assert asst["content"] is None
    assert asst["tool_calls"][0]["function"] == {"name": "search", "arguments": '{"query": "y"}'}
    assert body["messages"][2] == {"role": "tool", "content": "<untrusted_source>...</untrusted_source>",
                                   "tool_call_id": "c0"}
    assert r.tool_calls[0] == ToolCall(id="call_a", name="search", arguments={"query": "x"})
    assert r.tool_calls[1].arguments == {INVALID_ARGS_KEY: "{not json"}
    assert r.finish_reason == "tool_calls"


async def test_json_schema_mode_vs_prompt_fallback():
    rec = Recorder([httpx.Response(200, json=completion('{"label":"a","score":1}'))])
    c = make_openrouter(rec)
    await c.complete(make_req(response_schema=Out), "acme/json-model", {})
    rf = rec.requests[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["name"] == "Out"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False

    await c.complete(make_req(response_schema=Out), "acme/no-schema", {})
    body = rec.requests[1]
    assert "response_format" not in body
    assert body["messages"][0]["role"] == "system" and "JSON schema" in body["messages"][0]["content"]


async def test_cache_breakpoint_maps_to_cache_control_for_anthropic_only():
    rec = Recorder([httpx.Response(200, json=completion("ok"))])
    c = make_openrouter(rec)
    req = make_req("q")
    req.messages.insert(0, Message(role="system", content="long stable prefix", cache_breakpoint=True))
    await c.complete(req, "anthropic/claude-test", {})
    sys_msg = rec.requests[0]["messages"][0]
    assert sys_msg["content"] == [{"type": "text", "text": "long stable prefix",
                                   "cache_control": {"type": "ephemeral"}}]
    await c.complete(req, "acme/json-model", {})
    assert rec.requests[1]["messages"][0]["content"] == "long stable prefix"


async def test_retries_on_429_and_5xx_then_succeeds():
    rec = Recorder([
        httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rate"}),
        httpx.Response(503, text="busy"),
        httpx.Response(200, json=completion("ok")),
    ])
    r = await make_openrouter(rec).complete(make_req(), "acme/json-model", {})
    assert r.content == "ok" and len(rec.requests) == 3


async def test_retries_exhausted_and_non_retryable():
    rec = Recorder([httpx.Response(502, text="bad gateway")])
    with pytest.raises(RetryableProviderError):
        await make_openrouter(rec, max_retries=2).complete(make_req(), "acme/json-model", {})
    assert len(rec.requests) == 3
    rec2 = Recorder([httpx.Response(400, json={"error": {"message": "bad"}})])
    with pytest.raises(ProviderError) as ei:
        await make_openrouter(rec2).complete(make_req(), "acme/json-model", {})
    assert ei.value.status == 400 and len(rec2.requests) == 1


async def test_error_in_200_body_is_raised():
    rec = Recorder([httpx.Response(200, json={"error": {"code": 400, "message": "provider rejected"}})])
    with pytest.raises(ProviderError):
        await make_openrouter(rec).complete(make_req(), "acme/json-model", {})


def _chunk(delta=None, finish=None, usage=None):
    c = {"model": "acme/json-model", "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        c["usage"] = usage
        c["choices"] = []
    return c


async def test_streaming_text_tool_calls_and_usage():
    body = sse([
        _chunk({"role": "assistant", "content": "Hel"}),
        _chunk({"content": "lo"}),
        _chunk({"tool_calls": [{"index": 0, "id": "call_1",
                                "function": {"name": "sea", "arguments": '{"qu'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"name": "rch", "arguments": 'ery": "z"}'}}]}),
        _chunk(finish="tool_calls"),
        _chunk(usage={"prompt_tokens": 30, "completion_tokens": 9, "cost": 0.0001,
                      "prompt_tokens_details": {"cached_tokens": 0}}),
    ])
    rec = Recorder([httpx.Response(503, text="x"),
                    httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})])
    c = make_openrouter(rec)
    events = [e async for e in c.stream_complete(make_req(tools=[TOOL]), "acme/json-model", {})]
    assert rec.requests[-1]["stream"] is True
    assert [e.text for e in events if e.type == "text_delta"] == ["Hel", "lo"]
    tcs = [e.tool_call for e in events if e.type == "tool_call"]
    assert tcs == [ToolCall(id="call_1", name="search", arguments={"query": "z"})]
    done = events[-1]
    assert done.type == "done" and done.response.content == "Hello"
    assert done.response.usage.tokens_in == 30 and done.response.usage.cost_usd == pytest.approx(0.0001)
    assert done.response.finish_reason == "tool_calls"


async def test_stream_error_chunk():
    body = sse([_chunk({"content": "a"}), {"error": {"code": 502, "message": "upstream died"}}])
    rec = Recorder([httpx.Response(200, content=body)])
    events = [e async for e in make_openrouter(rec).stream_complete(make_req(), "acme/json-model", {})]
    assert events[-1].type == "error" and "upstream died" in events[-1].error


def test_estimated_cost_from_catalog():
    from app.contracts.llm import Usage

    c = make_openrouter(Recorder([httpx.Response(200, json={})]))
    est = c.estimate_cost("acme/json-model", Usage(tokens_in=1000, tokens_cached=400, tokens_out=100))
    assert est == pytest.approx(600 * 1e-7 + 400 * 1e-8 + 100 * 5e-7)
    assert c.estimate_cost("unknown/model", Usage(tokens_in=1)) is None
