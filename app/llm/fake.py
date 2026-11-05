"""FakeModelClient: a scripted `ModelClient` for other workstreams' tests. No network.

Usage:

    fake = FakeModelClient()
    fake.script("cos.plan", structured={"queries": []})            # validated against response_schema
    fake.script("cos.search_loop", tool_calls=[("search", {"query": "x"})])
    fake.script("cos.search_loop", text="done")                    # queued: next call on that role
    fake.script("voice.turn", text="Hello there.", chunks=3)       # streamed in 3 text deltas
    fake.default = FakeReply(text="ok")                            # fallback for unscripted roles
    fake.responder = lambda req: FakeReply(text=req.messages[-1].content)  # or compute replies

    resp = await fake.chat(req)
    fake.calls  # list[ChatRequest] actually received

Replies are consumed per role in FIFO order; the last reply for a role is sticky (repeats) unless
`sticky=False`. Budgets are enforced like the real router (pre-check + charge), so agents' budget
degradation paths can be tested. A structured reply that fails validation raises
`StructuredOutputError` (set `invalid_then_valid=True` to simulate a successful repair).
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from app.contracts.llm import ChatRequest, ChatResponse, StreamEvent, ToolCall, Usage
from app.llm import budget as B
from app.llm.errors import StructuredOutputError
from app.llm.ledger import Ledger, LedgerRecord
from app.llm.structured import parse_structured


@dataclass
class FakeReply:
    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] | list[ToolCall] = field(default_factory=list)
    structured: dict[str, Any] | None = None
    usage: Usage | None = None
    chunks: int = 1
    raise_exc: Exception | None = None
    invalid_then_valid: bool = False  # first output invalid, "repair" succeeds (2 calls charged)
    finish_reason: str = "stop"


class FakeModelClient:
    def __init__(self, *, model: str = "fake-model", provider: str = "fake", ledger: Ledger | None = None,
                 enforce_budget: bool = True):
        self.model = model
        self.provider = provider
        self.ledger = ledger
        self.enforce_budget = enforce_budget
        self.default: FakeReply | None = FakeReply(text="")
        self.responder: Callable[[ChatRequest], FakeReply | None] | None = None
        self.calls: list[ChatRequest] = []
        self._queues: dict[str, deque[FakeReply]] = defaultdict(deque)
        self._sticky: dict[str, FakeReply] = {}

    # ---- scripting -------------------------------------------------------------------------------

    def script(self, role: str, reply: FakeReply | None = None, *, sticky: bool = True, **kw: Any) -> None:
        r = reply or FakeReply(**kw)
        self._queues[role].append(r)
        if sticky:
            self._sticky[role] = r
        else:
            self._sticky.pop(role, None)

    def calls_for(self, role: str) -> list[ChatRequest]:
        return [c for c in self.calls if c.role == role]

    def _next(self, req: ChatRequest) -> FakeReply:
        if self.responder is not None:
            r = self.responder(req)
            if r is not None:
                return r
        q = self._queues.get(req.role)
        if q:
            return q.popleft()
        if req.role in self._sticky:
            return self._sticky[req.role]
        if self.default is None:
            raise AssertionError(f"FakeModelClient: no scripted reply for role {req.role!r}")
        return self.default

    # ---- ModelClient -----------------------------------------------------------------------------

    def _build(self, req: ChatRequest, r: FakeReply) -> ChatResponse:
        if r.raise_exc is not None:
            raise r.raise_exc
        tcs = [
            tc if isinstance(tc, ToolCall) else ToolCall(id=f"call_{i}", name=tc[0], arguments=tc[1])
            for i, tc in enumerate(r.tool_calls)
        ]
        content = r.text
        parsed = None
        n_calls = 1
        if req.response_schema is not None and not tcs:
            if r.structured is not None:
                content = json.dumps(r.structured)
            parsed, err = parse_structured(content, req.response_schema)
            if r.invalid_then_valid:
                n_calls = 2
            if parsed is None:
                raise StructuredOutputError(f"fake: invalid {req.response_schema.__name__}: {err}",
                                            raw=content, errors=err)
        est_in = B.estimate_input_tokens(req)
        usage = r.usage or Usage(tokens_in=est_in * n_calls,
                                 tokens_out=max(1, len(content) // 4) * n_calls, cost_usd=0.0)
        return ChatResponse(model=self.model, provider=self.provider, content=content, tool_calls=tcs,
                            parsed=parsed, usage=usage, latency_ms=0, finish_reason=r.finish_reason)

    def _account(self, req: ChatRequest, resp: ChatResponse, stream: bool) -> None:
        if self.enforce_budget:
            B.charge_actual(req.budget, resp.usage.tokens_in, resp.usage.tokens_out)
        if self.ledger is not None:
            t = req.trace
            self.ledger.record(LedgerRecord(
                role=req.role, provider=self.provider, model=self.model, tokens_in=resp.usage.tokens_in,
                tokens_out=resp.usage.tokens_out, cost_usd=resp.usage.cost_usd or 0.0, stream=stream,
                run_id=t.run_id, user_id=t.user_id, job_id=t.job_id, step=t.step,
                cache_namespace=req.cache_namespace, finish_reason=resp.finish_reason,
            ))

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.calls.append(req)
        if self.enforce_budget:
            B.precheck(req.budget, B.estimate_input_tokens(req))
        resp = self._build(req, self._next(req))
        self._account(req, resp, stream=False)
        return resp

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.calls.append(req)
        if self.enforce_budget:
            B.precheck(req.budget, B.estimate_input_tokens(req))
        reply = self._next(req)
        resp = self._build(req, reply)
        text = resp.content
        if text:
            n = max(1, reply.chunks)
            size = -(-len(text) // n)
            for i in range(0, len(text), size):
                yield StreamEvent(type="text_delta", text=text[i : i + size])
        for tc in resp.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)
        self._account(req, resp, stream=True)
        yield StreamEvent(type="done", response=resp)
