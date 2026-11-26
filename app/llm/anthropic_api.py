"""Direct Claude Messages API calls with Anthropic's server tools (web search, web fetch).

Used by the `anthropic` search provider (app/tools/search.py) and the Claude news-agent reference arm
(app/sources/claude_agent.py). Everything else in the model layer goes through `Router`; these two need
server tools, which OpenRouter does not expose, so they call the official `anthropic` SDK directly.

The SDK is an optional extra (`pip install -e ".[anthropic]"`) and is imported lazily. A missing key or
package raises `AnthropicUnavailable`; callers turn it into their own "unavailable" error. Nothing here
ever invents a result.

Docs checked 2026-11-26: platform.claude.com web-search-tool, web-fetch-tool, server-tools (pause_turn),
structured-outputs (incompatible with citations), refusals-and-fallback; SDK anthropic 2.14 (httpx2).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_BASE_URL = "https://api.anthropic.com"
WEB_SEARCH_TOOL = "web_search_20260318"
WEB_FETCH_TOOL = "web_fetch_20260209"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_USD = 10.0 / 1000  # $10 per 1,000 searches; web fetch has no per-call charge
# USD per million tokens: input, output, cache read, cache write (5 min). Anthropic list prices 2026-11.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


class AnthropicUnavailable(Exception):
    """No ANTHROPIC_API_KEY, or the `anthropic` package is not installed."""


class AnthropicCallError(Exception):
    """The API call failed (HTTP error, network, refusal). `code` is short and stable."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class AnthropicUsage(BaseModel):
    """Usage summed over every request of one logical call (pause_turn continuations, repairs)."""

    model: str
    served_by: list[str] = []  # response.model per request (differs from `model` after a fallback)
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0
    stop_reason: str | None = None
    latency_ms: int = 0

    def add(self, message: Any) -> None:
        self.requests += 1
        self.stop_reason = getattr(message, "stop_reason", None)
        served = getattr(message, "model", None)
        if served:
            self.served_by.append(str(served))
        u = getattr(message, "usage", None)
        if u is None:
            return
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        if stu is not None:
            self.web_search_requests += getattr(stu, "web_search_requests", 0) or 0
            self.web_fetch_requests += getattr(stu, "web_fetch_requests", 0) or 0

    @property
    def cost_known(self) -> bool:
        return self.model in PRICES

    @property
    def cost_usd(self) -> float:
        """List-price estimate (tokens at the requested model's rates + $0.01 per search).

        A fallback turn is billed at the fallback model's rates; we don't reprice it here.
        """
        p_in, p_out, p_cr, p_cw = PRICES.get(self.model, (0.0, 0.0, 0.0, 0.0))
        tokens = (
            self.input_tokens * p_in
            + self.output_tokens * p_out
            + self.cache_read_input_tokens * p_cr
            + self.cache_creation_input_tokens * p_cw
        ) / 1e6
        return round(tokens + self.web_search_requests * WEB_SEARCH_USD, 6)


def record_usage(ledger: Any, usage: AnthropicUsage, *, role: str, ok: bool = True, error: str | None = None):
    """Write one LedgerRecord for a logical call, if a `Ledger` was given."""
    if ledger is None:
        return
    from app.llm.ledger import LedgerRecord

    ledger.record(
        LedgerRecord(
            role=role,
            provider="anthropic",
            model=usage.model,
            tokens_in=usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens,
            tokens_out=usage.output_tokens,
            tokens_cached=usage.cache_read_input_tokens,
            cost_usd=usage.cost_usd,
            cost_source="estimated" if usage.cost_known else "none",
            latency_ms=usage.latency_ms,
            finish_reason=usage.stop_reason,
            ok=ok,
            error=error,
        )
    )


def make_async_client(api_key: str | None = None, *, timeout_s: float = 600.0, max_retries: int = 2) -> Any:
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise AnthropicUnavailable("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise AnthropicUnavailable(
            'the anthropic package is not installed (pip install -e ".[anthropic]")'
        ) from e
    # Pinned on purpose: the SDK would otherwise read ANTHROPIC_BASE_URL, which hosted agent containers
    # (e.g. Claude Code cloud sessions) set to their own endpoint. Override with ANTHROPIC_API_BASE_URL.
    base_url = os.environ.get("ANTHROPIC_API_BASE_URL") or DEFAULT_BASE_URL
    return anthropic.AsyncAnthropic(
        api_key=key, base_url=base_url, timeout=timeout_s, max_retries=max_retries
    )


def _sdk_error(e: Exception) -> AnthropicCallError | None:
    """Map an SDK exception to AnthropicCallError; None if `e` is not an SDK error."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return None
    if isinstance(e, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return AnthropicCallError("auth", f"Anthropic rejected the API key (HTTP {e.status_code})")
    if isinstance(e, anthropic.RateLimitError):
        return AnthropicCallError("rate_limited", "Anthropic rate limit (HTTP 429)")
    if isinstance(e, anthropic.APIStatusError):
        return AnthropicCallError("http", f"Anthropic returned HTTP {e.status_code}: {str(e.message)[:200]}")
    if isinstance(e, anthropic.APIConnectionError):  # includes APITimeoutError
        return AnthropicCallError("network", f"Anthropic request failed ({type(e).__name__})")
    return None


async def _one_request(client: Any, *, stream: bool, params: dict[str, Any]) -> Any:
    if stream:
        async with client.beta.messages.stream(**params) as s:
            return await s.get_final_message()
    return await client.beta.messages.create(**params)


async def run_turn(
    client: Any,
    *,
    usage: AnthropicUsage,
    messages: list[dict[str, Any]],
    stream: bool = False,
    max_continuations: int = 3,
    fallbacks: bool = True,
    **params: Any,
) -> list[Any]:
    """Run one assistant turn with server tools; returns every content block, in order.

    `pause_turn` is continued by re-sending the paused assistant content unchanged (same tools), at most
    `max_continuations` times; if it is still paused after that, the partial content is returned and
    `usage.stop_reason` stays "pause_turn". The server-side refusal fallback (`fallbacks="default"`) is
    on by default. Raises AnthropicCallError for HTTP / network errors.
    """
    msgs = list(messages)
    blocks: list[Any] = []
    extra: dict[str, Any] = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if fallbacks else {}
    t0 = time.monotonic()
    try:
        for i in range(max_continuations + 1):
            msg = await _one_request(client, stream=stream, params={**params, **extra, "messages": msgs})
            usage.add(msg)
            blocks.extend(msg.content)
            if msg.stop_reason != "pause_turn" or i == max_continuations:
                break
            msgs = [*msgs, {"role": "assistant", "content": msg.content}]
    except Exception as e:
        mapped = _sdk_error(e)
        if mapped is None:
            raise
        raise mapped from e
    finally:
        usage.latency_ms += int((time.monotonic() - t0) * 1000)
    return blocks


# ---- reading content blocks -------------------------------------------------------------------------


def as_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "to_dict"):
        return block.to_dict()
    return block.model_dump()  # pragma: no cover


def search_results(blocks: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(web_search_result dicts in order, deduped by url; error codes from error objects)."""
    out: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for b in map(as_dict, blocks):
        if b.get("type") != "web_search_tool_result":
            continue
        content = b.get("content")
        if isinstance(content, list):
            for r in content:
                if r.get("type") == "web_search_result" and r.get("url"):
                    out.setdefault(r["url"], r)
        elif isinstance(content, dict):  # web_search_tool_result_error: an object, not a list
            errors.append(str(content.get("error_code") or "unknown"))
    return list(out.values()), errors


def fetched_urls(blocks: list[Any]) -> list[str]:
    urls = []
    for b in map(as_dict, blocks):
        c = b.get("content")
        if (
            b.get("type") == "web_fetch_tool_result"
            and isinstance(c, dict)
            and c.get("type") == "web_fetch_result"
        ):
            if c.get("url"):
                urls.append(c["url"])
    return urls


def text_of(blocks: list[Any]) -> str:
    """All text blocks joined (citations split one answer into several text blocks)."""
    return "".join(b.get("text", "") for b in map(as_dict, blocks) if b.get("type") == "text")


def cited_text_by_url(blocks: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for b in map(as_dict, blocks):
        if b.get("type") != "text":
            continue
        for c in b.get("citations") or []:
            if c.get("type") == "web_search_result_location" and c.get("url") and c.get("cited_text"):
                out.setdefault(c["url"], c["cited_text"])
    return out


def extract_json(text: str) -> Any | None:
    """The longest JSON object or array in `text` (tolerates prose and ``` fences around it)."""
    dec = json.JSONDecoder()
    best: tuple[int, Any] | None = None
    i = 0
    while i < len(text):
        if text[i] not in "{[":
            i += 1
            continue
        try:
            val, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if best is None or end - i > best[0]:
            best = (end - i, val)
        i = end  # values nested inside this one are never longer
    return None if best is None else best[1]
