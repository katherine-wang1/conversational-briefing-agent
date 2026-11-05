"""OpenRouter provider (TDD §4.2) over OpenAI-compatible Chat Completions at https://openrouter.ai/api/v1.

- Tool calling in OpenAI function format.
- Structured output: `response_format: json_schema` when the model's catalog entry lists
  `structured_outputs`; otherwise the schema goes in the prompt (the router validates and repairs).
- Prompt caching: `Message.cache_breakpoint` becomes a `cache_control: {type: ephemeral}` content part
  for model families that need explicit breakpoints (Anthropic, Gemini). Other families
  (OpenAI, DeepSeek, Qwen, ...) cache stable prefixes implicitly; the hint is a no-op there.
- Usage: `usage.cost` (USD, provider-reported) and `prompt_tokens_details.cached_tokens`.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from app.contracts.common import Strict
from app.contracts.llm import ChatRequest, ChatResponse, Message, Usage
from app.llm.openai_compat import OpenAICompatClient

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EXPLICIT_CACHE_CONTROL_PREFIXES = ("anthropic/", "google/gemini")


class ModelInfo(Strict):
    id: str
    supported_parameters: list[str] = []
    prompt_usd_per_tok: float | None = None
    completion_usd_per_tok: float | None = None
    cache_read_usd_per_tok: float | None = None
    cache_write_usd_per_tok: float | None = None
    knowledge_cutoff: str | None = None
    context_length: int | None = None

    @classmethod
    def from_api(cls, m: dict[str, Any]) -> ModelInfo:
        p = m.get("pricing") or {}

        def f(k: str) -> float | None:
            v = p.get(k)
            return float(v) if v not in (None, "") else None

        return cls(
            id=m["id"], supported_parameters=m.get("supported_parameters") or [],
            prompt_usd_per_tok=f("prompt"), completion_usd_per_tok=f("completion"),
            cache_read_usd_per_tok=f("input_cache_read"), cache_write_usd_per_tok=f("input_cache_write"),
            knowledge_cutoff=m.get("knowledge_cutoff"), context_length=m.get("context_length"),
        )

    def estimate_cost(self, u: Usage) -> float | None:
        if self.prompt_usd_per_tok is None or self.completion_usd_per_tok is None:
            return None
        cached = u.tokens_cached
        read = self.cache_read_usd_per_tok
        if read is None:
            read = self.prompt_usd_per_tok
        return ((u.tokens_in - cached) * self.prompt_usd_per_tok + cached * read
                + u.tokens_out * self.completion_usd_per_tok)


class OpenRouterCatalog:
    """Model metadata from GET /api/v1/models (no auth needed). Loaded once, lazily."""

    def __init__(self, models: dict[str, ModelInfo] | None = None):
        self.models: dict[str, ModelInfo] = models or {}
        self.loaded = models is not None
        self._lock = asyncio.Lock()

    async def ensure(self, http: httpx.AsyncClient, base_url: str = OPENROUTER_BASE_URL) -> None:
        if self.loaded:
            return
        async with self._lock:
            if self.loaded:
                return
            try:
                r = await http.get(f"{base_url}/models")
                r.raise_for_status()
                self.models = {m["id"]: ModelInfo.from_api(m) for m in r.json().get("data", [])}
            except (httpx.HTTPError, ValueError, KeyError):
                self.models = {}  # unknown capabilities: assume support, provider will 400 if not
            self.loaded = True

    def get(self, model: str) -> ModelInfo | None:
        return self.models.get(model)


class OpenRouterClient(OpenAICompatClient):
    provider = "openrouter"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
        catalog: OpenRouterCatalog | None = None,
        app_title: str = "briefing-agent",
        **kw: Any,
    ):
        super().__init__(base_url=base_url, api_key=api_key or os.environ.get("OPENROUTER_API_KEY"), **kw)
        self.catalog = catalog or OpenRouterCatalog()
        self.app_title = app_title

    def headers(self) -> dict[str, str]:
        h = super().headers()
        h["X-Title"] = self.app_title
        return h

    def _info(self, model: str) -> ModelInfo | None:
        return self.catalog.get(model)

    def supports_json_schema(self, model: str) -> bool:
        info = self._info(model)
        if info is None:
            return True
        return "structured_outputs" in info.supported_parameters

    def supports_tools(self, model: str) -> bool:
        info = self._info(model)
        return info is None or "tools" in info.supported_parameters

    def map_message(self, m: Message, model: str) -> dict[str, Any]:
        out = super().map_message(m, model)
        if (m.cache_breakpoint and m.role in ("system", "user") and m.content
                and model.startswith(EXPLICIT_CACHE_CONTROL_PREFIXES)):
            out["content"] = [{"type": "text", "text": m.content, "cache_control": {"type": "ephemeral"}}]
        return out

    def extra_payload(self, req: ChatRequest, model: str, stream: bool) -> dict[str, Any]:
        return {"usage": {"include": True}}

    def estimate_cost(self, model: str, usage: Usage) -> float | None:
        info = self._info(model)
        return info.estimate_cost(usage) if info else None

    async def complete(self, req: ChatRequest, model: str, params: dict[str, Any]) -> ChatResponse:
        await self.catalog.ensure(self.http, self.base_url)
        return await super().complete(req, model, params)

    async def stream_complete(self, req: ChatRequest, model: str, params: dict[str, Any]):
        await self.catalog.ensure(self.http, self.base_url)
        async for ev in super().stream_complete(req, model, params):
            yield ev
