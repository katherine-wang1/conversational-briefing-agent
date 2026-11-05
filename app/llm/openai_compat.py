"""Transport for OpenAI-compatible Chat Completions over httpx (no SDK, no provider-side tools).

Shared by `OpenRouterClient` and `TinkerClient`. Provider clients are *transport only*: they take a
request plus a concrete model id and return a `ChatResponse` with usage. Caching, budgets, the
ledger and structured-output repair are applied once, in `app.llm.router.Router`.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import httpx

from app.contracts.llm import ChatRequest, ChatResponse, Message, StreamEvent, ToolCall, Usage
from app.llm.errors import ProviderError, RetryableProviderError
from app.llm.structured import response_format_for, with_schema_in_prompt

INVALID_ARGS_KEY = "__invalid_json__"  # ToolCall.arguments when the model emitted unparseable JSON


class ProviderClient(Protocol):
    """What the router needs from a provider. `params` are extra request-body fields from config."""

    provider: str

    async def complete(self, req: ChatRequest, model: str, params: dict[str, Any]) -> ChatResponse: ...

    def stream_complete(
        self, req: ChatRequest, model: str, params: dict[str, Any]
    ) -> AsyncIterator[StreamEvent]: ...

    def estimate_cost(self, model: str, usage: Usage) -> float | None: ...


def parse_tool_arguments(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except json.JSONDecodeError:
        return {INVALID_ARGS_KEY: raw}
    return v if isinstance(v, dict) else {INVALID_ARGS_KEY: raw}


class OpenAICompatClient:
    provider = "openai_compat"
    retry_statuses = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 120.0,
        max_retries: int = 4,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 20.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http = http or httpx.AsyncClient(timeout=timeout_s)
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self._sleep = sleep

    # ---- hooks -----------------------------------------------------------------------------------

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def supports_json_schema(self, model: str) -> bool:
        return True

    def supports_tools(self, model: str) -> bool:
        return True

    def map_message(self, m: Message, model: str) -> dict[str, Any]:
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            out["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in m.tool_calls
            ]
            if not m.content:
                out["content"] = None
        if m.role == "tool":
            out["tool_call_id"] = m.tool_call_id
        return out

    def parse_usage(self, usage: dict[str, Any] | None, model: str) -> Usage:
        if not usage:
            return Usage()
        details = usage.get("prompt_tokens_details") or {}
        cost = usage.get("cost")
        return Usage(
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            tokens_cached=int(details.get("cached_tokens") or 0),
            cost_usd=float(cost) if cost is not None else None,
        )

    def extra_payload(self, req: ChatRequest, model: str, stream: bool) -> dict[str, Any]:
        return {}

    def estimate_cost(self, model: str, usage: Usage) -> float | None:
        """Cost from a local price table when the provider reports none. None = unknown."""
        return None

    # ---- payload ---------------------------------------------------------------------------------

    def build_payload(self, req: ChatRequest, model: str, params: dict[str, Any], stream: bool) -> dict:
        messages = req.messages
        payload: dict[str, Any] = {"model": model, "max_tokens": req.max_output_tokens}
        if req.response_schema is not None:
            if self.supports_json_schema(model):
                payload["response_format"] = response_format_for(req.response_schema)
            else:
                messages = with_schema_in_prompt(messages, req.response_schema)
        payload["messages"] = [self.map_message(m, model) for m in messages]
        if req.tools:
            if not self.supports_tools(model):
                raise ProviderError(f"{self.provider}:{model} does not support tool calling")
            payload["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in req.tools
            ]
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if stream:
            payload["stream"] = True
        payload.update(self.extra_payload(req, model, stream))
        payload.update(params)
        return payload

    # ---- HTTP with retries -----------------------------------------------------------------------

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.backoff_max_s)
            except ValueError:
                pass
        return min(self.backoff_base_s * (2**attempt), self.backoff_max_s) * (0.5 + random.random() / 2)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.http.post(url, json=payload, headers=self.headers())
            except httpx.TransportError as e:
                last = RetryableProviderError(f"{self.provider} transport error: {e!r}")
                retry_after = None
            else:
                if r.status_code < 400:
                    data = r.json()
                    err = data.get("error") if isinstance(data, dict) else None
                    if err and not data.get("choices"):
                        code = err.get("code") if isinstance(err, dict) else None
                        msg = f"{self.provider} error in body: {str(err)[:500]}"
                        if isinstance(code, int) and code in self.retry_statuses:
                            last = RetryableProviderError(msg, status=code)
                            retry_after = None
                        else:
                            raise ProviderError(msg, status=code if isinstance(code, int) else None)
                    else:
                        return data
                elif r.status_code in self.retry_statuses:
                    last = RetryableProviderError(
                        f"{self.provider} HTTP {r.status_code}", status=r.status_code, body=r.text[:500]
                    )
                    retry_after = r.headers.get("retry-after")
                else:
                    raise ProviderError(
                        f"{self.provider} HTTP {r.status_code}: {r.text[:500]}",
                        status=r.status_code, body=r.text[:2000],
                    )
            if attempt < self.max_retries:
                await self._sleep(self._backoff(attempt, retry_after))
        assert last is not None
        raise last

    # ---- non-streaming ---------------------------------------------------------------------------

    def _response_from_message(self, data: dict[str, Any], model: str, latency_ms: int) -> ChatResponse:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.provider}: response has no choices: {str(data)[:300]}")
        ch = choices[0]
        msg = ch.get("message") or {}
        tcs = [
            ToolCall(id=tc.get("id") or f"call_{i}", name=tc["function"]["name"],
                     arguments=parse_tool_arguments(tc["function"].get("arguments")))
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        return ChatResponse(
            model=data.get("model") or model,
            provider=self.provider,
            content=msg.get("content") or "",
            tool_calls=tcs,
            usage=self.parse_usage(data.get("usage"), model),
            latency_ms=latency_ms,
            finish_reason=ch.get("finish_reason"),
        )

    async def complete(self, req: ChatRequest, model: str, params: dict[str, Any]) -> ChatResponse:
        payload = self.build_payload(req, model, params, stream=False)
        t0 = time.perf_counter()
        data = await self._post(payload)
        return self._response_from_message(data, model, int((time.perf_counter() - t0) * 1000))

    # ---- streaming -------------------------------------------------------------------------------

    async def _open_stream(self, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}/chat/completions"
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            retry_after = None
            try:
                req = self.http.build_request("POST", url, json=payload, headers=self.headers())
                r = await self.http.send(req, stream=True)
            except httpx.TransportError as e:
                last = RetryableProviderError(f"{self.provider} transport error: {e!r}")
            else:
                if r.status_code < 400:
                    return r
                body = (await r.aread()).decode(errors="replace")
                await r.aclose()
                if r.status_code in self.retry_statuses:
                    last = RetryableProviderError(f"{self.provider} HTTP {r.status_code}",
                                                  status=r.status_code, body=body[:500])
                    retry_after = r.headers.get("retry-after")
                else:
                    raise ProviderError(f"{self.provider} HTTP {r.status_code}: {body[:500]}",
                                        status=r.status_code, body=body[:2000])
            if attempt < self.max_retries:
                await self._sleep(self._backoff(attempt, retry_after))
        assert last is not None
        raise last

    async def stream_complete(
        self, req: ChatRequest, model: str, params: dict[str, Any]
    ) -> AsyncIterator[StreamEvent]:
        payload = self.build_payload(req, model, params, stream=True)
        t0 = time.perf_counter()
        r = await self._open_stream(payload)
        text: list[str] = []
        partial: dict[int, dict[str, Any]] = {}
        usage_raw: dict[str, Any] | None = None
        finish: str | None = None
        resp_model = model
        try:
            async for line in r.aiter_lines():
                if not line or line.startswith(":"):
                    continue  # blank keep-alive or SSE comment (": OPENROUTER PROCESSING")
                if not line.startswith("data:"):
                    continue
                data_s = line[5:].strip()
                if data_s == "[DONE]":
                    break
                chunk = json.loads(data_s)
                if chunk.get("error"):
                    yield StreamEvent(type="error", error=str(chunk["error"])[:500])
                    return
                resp_model = chunk.get("model") or resp_model
                if chunk.get("usage"):
                    usage_raw = chunk["usage"]
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        text.append(delta["content"])
                        yield StreamEvent(type="text_delta", text=delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = partial.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
        finally:
            await r.aclose()
        tool_calls = [
            ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments=parse_tool_arguments(s["args"]))
            for i, s in sorted(partial.items())
        ]
        for tc in tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)
        resp = ChatResponse(
            model=resp_model, provider=self.provider, content="".join(text), tool_calls=tool_calls,
            usage=self.parse_usage(usage_raw, model),
            latency_ms=int((time.perf_counter() - t0) * 1000), finish_reason=finish,
        )
        yield StreamEvent(type="done", response=resp)

    async def aclose(self) -> None:
        await self.http.aclose()
