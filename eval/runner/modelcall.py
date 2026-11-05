"""Shared helpers for evaluation-side model calls (simulator, judges, question generation).

All calls go through `app.contracts.llm.ModelClient` (build principle, PRD §6.0). Prompts are
versioned Markdown files with a small front-matter block (`role`, `version`); the prompt hash is
recorded with every output so a changed prompt is visible in results.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.llm import ChatRequest, ChatResponse, Message, ModelClient

T = TypeVar("T", bound=BaseModel)

_FM = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


@dataclass(frozen=True)
class Prompt:
    role: str
    version: str
    system: str
    user: Template
    sha: str
    path: str

    def render(self, **kw: str) -> tuple[str, str]:
        return self.system, self.user.substitute(**kw)


def load_prompt(path: Path) -> Prompt:
    """Prompt file format: front-matter, then `## system` and `## user` sections.

    The user section is a `string.Template` (`$name` placeholders) so JSON braces need no escaping.
    """
    raw = path.read_text()
    m = _FM.match(raw)
    if not m:
        raise ValueError(f"{path}: missing front-matter")
    meta = yaml.safe_load(m.group(1))
    body = m.group(2)
    parts = re.split(r"^## (system|user)\s*$", body, flags=re.M)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise ValueError(f"{path}: needs exactly '## system' and '## user' sections")
    return Prompt(
        role=meta["role"],
        version=str(meta["version"]),
        system=sections["system"],
        user=Template(sections["user"]),
        sha=hashlib.sha256(raw.encode()).hexdigest()[:12],
        path=str(path),
    )


class StructuredCallError(RuntimeError):
    pass


def _parse(resp: ChatResponse, schema: type[T]) -> T:  # noqa: UP047
    if resp.parsed is not None:
        return schema.model_validate(resp.parsed)
    text = resp.content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise StructuredCallError(f"no JSON object in model output: {text[:200]!r}")
    return schema.model_validate(json.loads(text[start : end + 1]))


async def call_structured(  # noqa: UP047
    client: ModelClient,
    *,
    role: str,
    system: str,
    user: str,
    schema: type[T],
    trace: TraceCtx,
    cache_namespace: str,
    budget: TokenBudget | None = None,
    temperature: float | None = None,
    max_output_tokens: int = 1024,
    retries: int = 1,
) -> tuple[T, ChatResponse]:
    """One structured call with a single repair retry on schema failure."""
    budget = budget or TokenBudget(input_tokens=200_000)
    messages = [
        Message(role="system", content=system, cache_breakpoint=True),
        Message(role="user", content=user),
    ]
    last_err: Exception | None = None
    for _ in range(retries + 1):
        req = ChatRequest(
            role=role,
            messages=messages,
            response_schema=schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            budget=budget,
            trace=trace.model_copy(update={"role": role}),
            cache_namespace=cache_namespace,
        )
        resp = await client.chat(req)
        try:
            return _parse(resp, schema), resp
        except (ValidationError, ValueError, StructuredCallError) as e:
            last_err = e
            messages = [
                *messages,
                Message(role="assistant", content=resp.content),
                Message(
                    role="user",
                    content=f"That did not match the required JSON schema ({e}). "
                    "Reply with only the corrected JSON object.",
                ),
            ]
    raise StructuredCallError(f"{role}: structured output failed after retries: {last_err}")
