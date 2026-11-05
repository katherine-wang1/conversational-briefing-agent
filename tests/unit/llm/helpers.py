"""Mock-HTTP helpers for model-layer tests (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.llm import ChatRequest, Message
from app.llm.openrouter import ModelInfo, OpenRouterCatalog, OpenRouterClient

CATALOG = OpenRouterCatalog({
    "acme/json-model": ModelInfo(id="acme/json-model", supported_parameters=["tools", "structured_outputs"],
                                 prompt_usd_per_tok=1e-7, completion_usd_per_tok=5e-7,
                                 cache_read_usd_per_tok=1e-8),
    "acme/no-schema": ModelInfo(id="acme/no-schema", supported_parameters=["tools"],
                                prompt_usd_per_tok=1e-7, completion_usd_per_tok=5e-7),
    "anthropic/claude-test": ModelInfo(id="anthropic/claude-test",
                                       supported_parameters=["tools", "structured_outputs"]),
})


def completion(content: str | None = "hi", *, tool_calls: list[dict] | None = None,
               usage: dict[str, Any] | None = None, model: str = "acme/json-model",
               finish: str = "stop") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": "gen-1", "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
    }


def sse(chunks: list[dict[str, Any]], *, comment: bool = True) -> bytes:
    lines = [": OPENROUTER PROCESSING", ""] if comment else []
    for c in chunks:
        lines += [f"data: {json.dumps(c)}", ""]
    lines += ["data: [DONE]", ""]
    return ("\n".join(lines) + "\n").encode()


class Recorder:
    """httpx.MockTransport handler serving queued responses and recording request bodies."""

    def __init__(self, responses: list[httpx.Response | Callable[[httpx.Request], httpx.Response]]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content) if request.content else {})
        self.headers.append(request.headers)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r(request) if callable(r) else r


async def no_sleep(_: float) -> None:
    return None


def make_openrouter(rec: Recorder, **kw: Any) -> OpenRouterClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    return OpenRouterClient(api_key="test-key", http=http, catalog=CATALOG, sleep=no_sleep, **kw)


def make_req(content: str = "hello", *, role: str = "cos.score", budget: TokenBudget | None = None,
             **kw: Any) -> ChatRequest:
    return ChatRequest(role=role, messages=[Message(role="user", content=content)],
                       budget=budget or TokenBudget(input_tokens=100_000),
                       trace=TraceCtx(run_id="r1", job_id="j1"), **kw)
