"""Router: the `ModelClient` agents use. Picks provider + model per logical role
(`loader.models().for_role()`), and wraps every call with the call cache, token budget, spend cap,
cost ledger, and structured-output validation with one repair retry (TDD §4.4–4.6).
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.config import loader
from app.config.loader import ModelRole, ModelsCfg
from app.contracts.llm import ChatRequest, ChatResponse, StreamEvent, Usage
from app.llm import budget as B
from app.llm.cache import CallCache, cache_key
from app.llm.errors import StructuredOutputError
from app.llm.ledger import JsonlLedgerSink, Ledger, LedgerRecord, LedgerSink
from app.llm.openai_compat import ProviderClient
from app.llm.structured import parse_structured, repair_messages


def _sum_usage(a: Usage, b: Usage) -> Usage:
    cost = None if a.cost_usd is None and b.cost_usd is None else (a.cost_usd or 0) + (b.cost_usd or 0)
    return Usage(tokens_in=a.tokens_in + b.tokens_in, tokens_out=a.tokens_out + b.tokens_out,
                 tokens_cached=a.tokens_cached + b.tokens_cached, cost_usd=cost)


class Router:
    def __init__(
        self,
        providers: dict[str, ProviderClient],
        *,
        ledger: Ledger | None = None,
        cache: CallCache | None = None,
        models_cfg: ModelsCfg | None = None,
        cache_live: bool = False,
    ):
        self.providers = providers
        self.ledger = ledger or Ledger()
        self.cache = cache
        self._models_cfg = models_cfg
        self.cache_live = cache_live  # TDD §4.5: cache is off for namespace "live" unless enabled

    @classmethod
    def from_env(
        cls,
        *,
        ledger_path: str | Path | None = None,
        cache_path: str | Path | None = None,
        spend_cap_usd: float | None = None,
        ledger: Ledger | None = None,
        **kw: Any,
    ) -> Router:
        """Production wiring: keys from OPENROUTER_API_KEY / TINKER_API_KEY, JSONL ledger at
        `ledger_path` (or $LLM_LEDGER_PATH), optional SQLite call cache."""
        from app.llm.openrouter import OpenRouterClient
        from app.llm.tinker import TinkerClient

        if ledger is None:
            path = ledger_path or os.environ.get("LLM_LEDGER_PATH", "eval/runs/llm_ledger.jsonl")
            sinks: list[LedgerSink] = [JsonlLedgerSink(path)]
            ledger = Ledger(sinks, spend_cap_usd=spend_cap_usd)
        return cls(
            {"openrouter": OpenRouterClient(), "tinker": TinkerClient()},
            ledger=ledger,
            cache=CallCache(cache_path) if cache_path else None,
            **kw,
        )

    # ---- resolution ------------------------------------------------------------------------------

    @property
    def models_cfg(self) -> ModelsCfg:
        return self._models_cfg or loader.models()

    def resolve(self, role: str) -> ModelRole:
        return self.models_cfg.for_role(role)

    def bind(self, provider: str, model: str, params: dict[str, Any] | None = None) -> BoundModelClient:
        """A `ModelClient` pinned to one provider/model regardless of role (used by the bake-off)."""
        return BoundModelClient(self, ModelRole(provider=provider, model=model, params=params or {}))

    # ---- ModelClient -----------------------------------------------------------------------------

    async def chat(self, req: ChatRequest) -> ChatResponse:
        return await self.chat_with(req, self.resolve(req.role))

    def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        return self.stream_with(req, self.resolve(req.role))

    # ---- internals -------------------------------------------------------------------------------

    def _use_cache(self, req: ChatRequest) -> bool:
        return self.cache is not None and (req.cache_namespace != "live" or self.cache_live)

    def _record(self, req: ChatRequest, mr: ModelRole, resp: ChatResponse | None, *, kind: str = "call",
                stream: bool = False, ttft_ms: int | None = None, error: str | None = None,
                latency_ms: int = 0) -> None:
        u = resp.usage if resp else Usage()
        cost, source = u.cost_usd, "provider"
        if kind == "cache_hit":
            cost, source = 0.0, "cache"
        elif cost is None:
            est = self.providers[mr.provider].estimate_cost(mr.model, u) if resp else None
            cost, source = (est, "estimated") if est is not None else (0.0, "none")
        t = req.trace
        self.ledger.record(LedgerRecord(
            kind=kind, role=req.role, provider=mr.provider, model=resp.model if resp else mr.model,  # type: ignore[arg-type]
            tokens_in=u.tokens_in, tokens_out=u.tokens_out, tokens_cached=u.tokens_cached,
            cost_usd=cost, cost_source=source,  # type: ignore[arg-type]
            latency_ms=resp.latency_ms if resp else latency_ms, ttft_ms=ttft_ms, stream=stream,
            run_id=t.run_id, user_id=t.user_id, job_id=t.job_id, step=t.step,
            cache_namespace=req.cache_namespace, finish_reason=resp.finish_reason if resp else None,
            ok=error is None, error=error,
        ))

    def _with_cost(self, mr: ModelRole, resp: ChatResponse) -> ChatResponse:
        if resp.usage.cost_usd is None:
            est = self.providers[mr.provider].estimate_cost(mr.model, resp.usage)
            if est is not None:
                resp = resp.model_copy(update={"usage": resp.usage.model_copy(update={"cost_usd": est})})
        return resp

    def _cache_hit(self, req: ChatRequest, key: str) -> ChatResponse | None:
        if not self._use_cache(req):
            return None
        assert self.cache is not None
        hit = self.cache.get(key)
        if hit is not None:
            # Hits are charged to the token budget like real calls so a cached re-run behaves identically.
            B.precheck(req.budget, hit.usage.tokens_in)
            B.charge_actual(req.budget, hit.usage.tokens_in, hit.usage.tokens_out)
        return hit

    async def _call(self, req: ChatRequest, mr: ModelRole, kind: str = "call") -> ChatResponse:
        B.precheck(req.budget, B.estimate_input_tokens(req))
        self.ledger.check_cap()
        provider = self.providers[mr.provider]
        t0 = time.perf_counter()
        try:
            resp = await provider.complete(req, mr.model, dict(mr.params))
        except Exception as e:
            self._record(req, mr, None, kind=kind, error=f"{type(e).__name__}: {e}"[:500],
                         latency_ms=int((time.perf_counter() - t0) * 1000))
            raise
        self._record(req, mr, resp, kind=kind)  # before _with_cost so cost_source stays accurate
        resp = self._with_cost(mr, resp)
        B.charge_actual(req.budget, resp.usage.tokens_in, resp.usage.tokens_out)
        return resp

    async def _validate_or_repair(self, req: ChatRequest, mr: ModelRole, resp: ChatResponse) -> ChatResponse:
        if req.response_schema is None or resp.tool_calls:
            return resp
        parsed, errors = parse_structured(resp.content, req.response_schema)
        if parsed is not None:
            return resp.model_copy(update={"parsed": parsed})
        repair_req = req.model_copy(
            update={"messages": repair_messages(req.messages, resp.content, errors, req.response_schema)}
        )
        resp2 = await self._call(repair_req, mr, kind="repair")
        parsed2, errors2 = parse_structured(resp2.content, req.response_schema)
        if parsed2 is None:
            raise StructuredOutputError(
                f"{mr.provider}:{mr.model} failed {req.response_schema.__name__} after repair: {errors2}",
                raw=resp2.content, errors=errors2,
            )
        return resp2.model_copy(update={
            "parsed": parsed2,
            "usage": _sum_usage(resp.usage, resp2.usage),
            "latency_ms": resp.latency_ms + resp2.latency_ms,
        })

    async def chat_with(self, req: ChatRequest, mr: ModelRole) -> ChatResponse:
        key = cache_key(mr.provider, mr.model, req, dict(mr.params))
        hit = self._cache_hit(req, key)
        if hit is not None:
            self._record(req, mr, hit, kind="cache_hit")
            return hit
        resp = await self._call(req, mr)
        resp = await self._validate_or_repair(req, mr, resp)
        if self._use_cache(req):
            assert self.cache is not None
            self.cache.put(key, req.cache_namespace, resp)
        return resp

    async def stream_with(self, req: ChatRequest, mr: ModelRole) -> AsyncIterator[StreamEvent]:
        key = cache_key(mr.provider, mr.model, req, dict(mr.params))
        hit = self._cache_hit(req, key)
        if hit is not None:
            self._record(req, mr, hit, kind="cache_hit", stream=True, ttft_ms=0)
            if hit.content:
                yield StreamEvent(type="text_delta", text=hit.content)
            for tc in hit.tool_calls:
                yield StreamEvent(type="tool_call", tool_call=tc)
            yield StreamEvent(type="done", response=hit)
            return

        B.precheck(req.budget, B.estimate_input_tokens(req))
        self.ledger.check_cap()
        provider = self.providers[mr.provider]
        t0 = time.perf_counter()
        ttft: int | None = None
        final: ChatResponse | None = None
        try:
            async for ev in provider.stream_complete(req, mr.model, dict(mr.params)):
                if ev.type in ("text_delta", "tool_call") and ttft is None:
                    ttft = int((time.perf_counter() - t0) * 1000)
                if ev.type == "error":
                    self._record(req, mr, None, stream=True, ttft_ms=ttft, error=ev.error,
                                 latency_ms=int((time.perf_counter() - t0) * 1000))
                    yield ev
                    return
                if ev.type == "done":
                    final = ev.response
                    break
                yield ev
        except Exception as e:
            self._record(req, mr, None, stream=True, ttft_ms=ttft, error=f"{type(e).__name__}: {e}"[:500],
                         latency_ms=int((time.perf_counter() - t0) * 1000))
            raise
        if final is None:
            yield StreamEvent(type="error", error="stream ended without a final response")
            return
        self._record(req, mr, final, stream=True, ttft_ms=ttft)
        final = self._with_cost(mr, final)
        B.charge_actual(req.budget, final.usage.tokens_in, final.usage.tokens_out)
        final = await self._validate_or_repair(req, mr, final)
        if self._use_cache(req):
            assert self.cache is not None
            self.cache.put(key, req.cache_namespace, final)
        yield StreamEvent(type="done", response=final)


class BoundModelClient:
    """`ModelClient` that always uses one provider/model (bake-off, spikes)."""

    def __init__(self, router: Router, mr: ModelRole):
        self.router = router
        self.model_role = mr

    async def chat(self, req: ChatRequest) -> ChatResponse:
        return await self.router.chat_with(req, self.model_role)

    def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        return self.router.stream_with(req, self.model_role)
