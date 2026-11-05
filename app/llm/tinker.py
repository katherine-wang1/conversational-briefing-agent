"""Tinker provider (TDD §4.2) for `sim.*` and `judge.*` roles.

Uses Tinker's OpenAI-compatible API (beta) over httpx, where the server applies the model's chat
template. Base URL: https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1 (the same host
the official `tinker` SDK defaults to; `api.tinker.thinkingmachines.ai` is blocked from the build
container). Override with TINKER_OAI_BASE_URL.

- No tool calling (not needed for simulators/judges; requests with tools are rejected).
- Structured output: schema in the prompt; the router validates and repairs.
- Cost: Tinker does not return a cost field, so cost comes from the price table below
  (prefill / cached prefill / sample, USD per 1M tokens), using the response's cached-token count.
"""

from __future__ import annotations

import os
from typing import Any

from app.contracts.llm import ChatRequest, Usage
from app.llm.openai_compat import OpenAICompatClient

TINKER_OAI_BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"

# Short names used in config/models.yaml -> Tinker model ids.
MODEL_ALIASES: dict[str, str] = {
    "inkling-small": "thinkingmachines/Inkling-Small:peft:262144:sampling-nvfp4",
    "inkling": "thinkingmachines/Inkling:peft:262144:sampling-nvfp4",
}

# USD per 1M tokens: (prefill, cached prefill, sample). Source: thinkingmachines.ai/tinker/pricing,
# prices "as of October 19, 2026". Serverless rows apply to the `:sampling-nvfp4` ids.
PRICES_PER_M: dict[str, tuple[float, float, float]] = {
    "thinkingmachines/Inkling-Small:peft:262144:sampling-nvfp4": (0.30, 0.06, 1.20),
    "thinkingmachines/Inkling:peft:262144:sampling-nvfp4": (1.00, 0.17, 4.05),
    "thinkingmachines/Inkling-Small": (0.58, 0.116, 1.44),
    "thinkingmachines/Inkling": (1.87, 0.374, 4.68),
    "Qwen/Qwen3-8B": (0.195, 0.039, 0.60),
    "Qwen/Qwen3.5-4B": (0.33, 0.066, 1.005),
    "Qwen/Qwen3.5-9B": (0.30, 0.06, 0.807),
    "Qwen/Qwen3.6-35B-A3B": (0.45, 0.09, 1.335),
    "openai/gpt-oss-20b": (0.18, 0.036, 0.45),
    "openai/gpt-oss-120b": (0.33, 0.066, 0.84),
}


def resolve_model(model: str) -> str:
    return MODEL_ALIASES.get(model, model)


def price_cost(model: str, u: Usage) -> float | None:
    p = PRICES_PER_M.get(resolve_model(model)) or PRICES_PER_M.get(resolve_model(model).split(":")[0])
    if p is None:
        return None
    prefill, cached, sample = p
    fresh = max(u.tokens_in - u.tokens_cached, 0)
    return (fresh * prefill + u.tokens_cached * cached + u.tokens_out * sample) / 1e6


class TinkerClient(OpenAICompatClient):
    provider = "tinker"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, **kw: Any):
        super().__init__(
            base_url=base_url or os.environ.get("TINKER_OAI_BASE_URL") or TINKER_OAI_BASE_URL,
            api_key=api_key or os.environ.get("TINKER_API_KEY"),
            **kw,
        )

    def supports_json_schema(self, model: str) -> bool:
        return False  # not documented for Tinker's OAI endpoint: schema-in-prompt + validation

    def supports_tools(self, model: str) -> bool:
        return False

    def build_payload(self, req: ChatRequest, model: str, params: dict[str, Any], stream: bool) -> dict:
        return super().build_payload(req, resolve_model(model), params, stream)

    def parse_usage(self, usage: dict[str, Any] | None, model: str) -> Usage:
        u = super().parse_usage(usage, model)
        if usage and not u.tokens_cached:
            # Tinker-native name for prefix-cache hits, if the OAI layer surfaces it.
            hits = usage.get("prompt_cache_hit_tokens")
            if hits:
                u = u.model_copy(update={"tokens_cached": int(hits)})
        return u

    def estimate_cost(self, model: str, usage: Usage) -> float | None:
        return price_cost(model, usage)
