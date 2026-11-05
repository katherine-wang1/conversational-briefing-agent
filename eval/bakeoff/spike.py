"""Week-1 model spike (TDD §27 spikes 1, 2, 5). LIVE: spends real money; run deliberately.

    python -m eval.bakeoff.spike --live [--cap 1.0] [--models a,b] [--tinker inkling-small]

Per OpenRouter model: tool calling, JSON-schema mode (with repair accounting), prompt-caching fields
in usage (two identical calls over a ~4.5K-token prefix with a cache breakpoint), the cost field,
time to first token (streaming, 2 reps), and a self-reported training cutoff. Per Tinker model:
latency, streaming TTFT, prompt-mode JSON, caching discount, self-reported cutoff.

Every call goes through `Router` -> ledger (JSONL) with a hard spend cap. Raw usage blocks are
captured via an httpx event hook so undocumented fields (cache_write_tokens, reasoning tokens) show up.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.llm import ChatRequest, Message
from app.contracts.tools import ToolSpec
from app.llm.errors import LLMError
from app.llm.ledger import JsonlLedgerSink, Ledger
from app.llm.openrouter import OpenRouterClient
from app.llm.router import Router
from app.llm.tinker import TinkerClient

DEFAULT_MODELS = [
    "anthropic/claude-haiku-5.5",
    "openai/gpt-6-luna",
    "google/gemini-3.5-flash-lite",
    "deepseek/deepseek-v4.1-flash",
    "qwen/qwen3.8-flash",
    "mistralai/mistral-small-2603",
    "anthropic/claude-sonnet-5.5",  # mid-tier reference
]
OUT_DIR = Path(__file__).parent / "spike_2026-11-05"

SEARCH_TOOL = ToolSpec(
    name="search",
    description="Search recent news. Returns headlines.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
        "required": ["query"],
    },
)


class Verdict(BaseModel):
    label: Literal["relevant", "irrelevant"]
    confidence: float
    reasons: list[str]


def long_prefix() -> str:
    lines = [f"Style rule {i}: when a story mentions item {i * 7 % 101}, prefer the source with code "
             f"R{i:03d}-{(i * 37) % 997} and keep sentences under {12 + i % 9} words." for i in range(1, 260)]
    return "You are a newsroom assistant. Follow the style guide.\n" + "\n".join(lines)


def budget() -> TokenBudget:
    return TokenBudget(input_tokens=200_000)


def req(role: str, messages: list[Message], **kw: Any) -> ChatRequest:
    return ChatRequest(role=role, messages=messages, budget=budget(),
                       trace=TraceCtx(run_id="spike-2026-11-05", step=role), **kw)


class RawUsage:
    """httpx response hook: remembers the last non-streaming `usage` block verbatim."""

    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None

    async def hook(self, r: httpx.Response) -> None:
        if r.request.method == "POST" and "chat/completions" in str(r.url):
            if "text/event-stream" in r.headers.get("content-type", ""):
                return
            await r.aread()
            try:
                self.last = r.json().get("usage")
            except ValueError:
                self.last = None


async def probe_model(router: Router, provider: str, model: str, raw: RawUsage,
                      params: dict[str, Any] | None = None) -> dict[str, Any]:
    c = router.bind(provider, model, params or {})
    out: dict[str, Any] = {"provider": provider, "model": model}

    async def guarded(name: str, coro):
        try:
            out[name] = await coro
        except LLMError as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"[:400]}
        except Exception as e:  # noqa: BLE001 - spike: record and continue
            out[name] = {"error": f"{type(e).__name__}: {e}"[:400]}

    async def tools():
        if provider == "tinker":
            return {"skipped": "no tool calling on Tinker OAI endpoint"}
        r = await c.chat(req("spike.tools", [
            Message(role="system", content="You can call tools. Use them when asked."),
            Message(role="user", content="Use the search tool to look up news about solid-state batteries."),
        ], tools=[SEARCH_TOOL], max_output_tokens=400))
        tc = r.tool_calls[0] if r.tool_calls else None
        return {"ok": bool(tc and tc.name == "search" and isinstance(tc.arguments.get("query"), str)),
                "tool_calls": [t.model_dump() for t in r.tool_calls], "finish": r.finish_reason,
                "usage": r.usage.model_dump(), "raw_usage": raw.last, "latency_ms": r.latency_ms}

    async def json_mode():
        n0 = router.ledger.n_calls
        r = await c.chat(req("spike.json", [
            Message(role="system", content="Classify relevance for a reader interested in AI chips."),
            Message(role="user", content="Headline: 'Startup tapes out 3nm inference accelerator'."),
        ], response_schema=Verdict, max_output_tokens=400))
        return {"ok": r.parsed is not None, "calls": router.ledger.n_calls - n0, "parsed": r.parsed,
                "usage": r.usage.model_dump(), "raw_usage": raw.last, "latency_ms": r.latency_ms}

    async def caching():
        prefix = long_prefix()
        res = []
        for i in range(2):
            r = await c.chat(req("spike.cache", [
                Message(role="system", content=prefix, cache_breakpoint=True),
                Message(role="user", content="Reply with the word OK."),
            ], max_output_tokens=20 if provider != "tinker" else 8))
            res.append({"usage": r.usage.model_dump(), "raw_usage": raw.last, "latency_ms": r.latency_ms})
            if i == 0:
                await asyncio.sleep(2)
        return res

    async def ttft():
        res = []
        for _ in range(2):
            t0 = time.perf_counter()
            first = None
            done = None
            async for ev in c.stream(req("spike.stream", [
                Message(role="user", content="In one sentence, what is a power purchase agreement?"),
            ], max_output_tokens=60)):
                if first is None and ev.type == "text_delta":
                    first = int((time.perf_counter() - t0) * 1000)
                if ev.type == "done":
                    done = ev.response
                if ev.type == "error":
                    return {"error": ev.error}
            res.append({"ttft_ms": first, "total_ms": int((time.perf_counter() - t0) * 1000),
                        "usage": done.usage.model_dump() if done else None})
        vals = [x["ttft_ms"] for x in res if x["ttft_ms"] is not None]
        return {"runs": res, "ttft_median_ms": statistics.median(vals) if vals else None}

    async def cutoff():
        r = await c.chat(req("spike.cutoff", [
            Message(role="user", content="What is your training data cutoff date? Answer with month and "
                                         "year only, in one short line."),
        ], max_output_tokens=300))
        return {"answer": r.content.strip()[:200], "usage": r.usage.model_dump()}

    await guarded("tools", tools())
    await guarded("json_schema", json_mode())
    await guarded("caching", caching())
    await guarded("ttft", ttft())
    await guarded("cutoff_self_reported", cutoff())
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="required: this spends money")
    ap.add_argument("--cap", type=float, default=1.0, help="hard spend cap in USD for this run")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--tinker", default="inkling-small")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args()
    if not a.live:
        raise SystemExit("refusing to run without --live (this spike spends real money)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = RawUsage()
    http = httpx.AsyncClient(timeout=120, event_hooks={"response": [raw.hook]})
    ledger = Ledger([JsonlLedgerSink(out / "ledger.jsonl")], spend_cap_usd=a.cap)
    router = Router({"openrouter": OpenRouterClient(http=http, max_retries=2),
                     "tinker": TinkerClient(http=http, max_retries=2)}, ledger=ledger)
    results = []
    for m in [x for x in a.models.split(",") if x]:
        print(f"== openrouter {m}", flush=True)
        results.append(await probe_model(router, "openrouter", m, raw))
        print(f"   spent so far ${ledger.total_cost_usd:.5f}", flush=True)
    if a.tinker:
        for m in a.tinker.split(","):
            print(f"== tinker {m}", flush=True)
            results.append(await probe_model(router, "tinker", m, raw, {"reasoning_effort": "none"}))
            print(f"   spent so far ${ledger.total_cost_usd:.5f}", flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    print(f"total ledger spend ${ledger.total_cost_usd:.5f} over {ledger.n_calls} calls")
    await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
