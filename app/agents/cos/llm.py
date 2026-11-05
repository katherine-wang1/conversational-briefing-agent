"""Thin helpers over `ModelClient` used by every per-user agent step.

Budgets: the model layer (W-B) charges `TokenBudget` around each call and raises `BudgetExceeded`.
These helpers never charge tokens themselves; they let `BudgetExceeded` propagate so each step can
degrade gracefully.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.agents.cos.prompts import Prompt
from app.agents.cos.trace import RunTrace
from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.llm import ChatRequest, ChatResponse, Message, ModelClient
from app.contracts.tools import ToolSpec


class StructuredOutputFailure(Exception):
    """The model's reply could not be validated against the expected schema."""


def as_uuid(user_id: str | None) -> UUID | None:
    if not user_id:
        return None
    try:
        return UUID(str(user_id))
    except ValueError:
        return None


@dataclass
class CallCtx:
    """Per-run settings shared by every model call of one agent run (budget and trace are shared refs)."""

    client: Any  # ModelClient
    budget: TokenBudget
    trace: RunTrace
    run_id: str = "live"
    user_id: str | None = None
    job_id: str | None = None
    cache_namespace: str = "live"

    def trace_ctx(self, role: str, step: str) -> TraceCtx:
        return TraceCtx(
            run_id=self.run_id, user_id=as_uuid(self.user_id), job_id=self.job_id, role=role, step=step
        )


def render_payload(payload: str | dict | list) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=1, default=str)


def system_message(prompt: Prompt) -> Message:
    return Message(role="system", content=prompt.body, cache_breakpoint=True)


async def chat(
    cc: CallCtx,
    prompt: Prompt,
    messages: list[Message],
    *,
    step: str,
    tools: list[ToolSpec] | None = None,
    schema: type[BaseModel] | None = None,
    max_output_tokens: int = 2048,
) -> ChatResponse:
    client: ModelClient = cc.client
    req = ChatRequest(
        role=prompt.role,
        messages=messages,
        tools=tools,
        response_schema=schema,
        max_output_tokens=max_output_tokens,
        temperature=0.0,
        budget=cc.budget,
        trace=cc.trace_ctx(prompt.role, step),
        cache_namespace=cc.cache_namespace,
    )
    cc.trace.prompts[prompt.name] = prompt.sha
    resp = await client.chat(req)
    cc.trace.add(
        step,
        "llm",
        role=prompt.role,
        prompt=prompt.name,
        model=resp.model,
        tokens_in=resp.usage.tokens_in,
        tokens_out=resp.usage.tokens_out,
        tool_calls=[t.name for t in resp.tool_calls],
        cached=resp.cached,
    )
    return resp


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_structured[T: BaseModel](resp: ChatResponse, schema: type[T]) -> T:
    try:
        if resp.parsed is not None:
            return schema.model_validate(resp.parsed)
        text = _FENCE.sub("", resp.content.strip())
        return schema.model_validate(json.loads(text))
    except (ValidationError, json.JSONDecodeError, TypeError) as e:
        raise StructuredOutputFailure(f"{schema.__name__}: {e}") from e


async def call_structured[T: BaseModel](
    cc: CallCtx,
    prompt: Prompt,
    payload: str | dict | list,
    schema: type[T],
    *,
    step: str,
    max_output_tokens: int = 2048,
) -> T:
    """One structured call: system prompt + one user message → validated `schema` instance."""
    messages = [system_message(prompt), Message(role="user", content=render_payload(payload))]
    resp = await chat(cc, prompt, messages, step=step, schema=schema, max_output_tokens=max_output_tokens)
    try:
        return parse_structured(resp, schema)
    except StructuredOutputFailure as e:
        cc.trace.add(step, "llm", role=prompt.role, ok=False, error=str(e)[:300])
        raise
