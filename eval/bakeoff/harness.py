"""Bake-off harness (TDD §19.4): run fixed fixtures per role across candidate models.

Every call is streamed through the `Router` (so cache, budget, ledger and repair behave exactly as
in production) and records: schema validity (first try / after repair), tool-call success, time to
first token, latency, tokens, cost, and the raw output for later judging.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.llm import ChatRequest, ModelClient
from app.llm.openai_compat import INVALID_ARGS_KEY
from eval.bakeoff.fixtures import Case


@dataclass
class Candidate:
    provider: str
    model: str

    @classmethod
    def parse(cls, s: str) -> Candidate:
        if ":" in s.split("/")[0]:  # "tinker:inkling-small"
            p, m = s.split(":", 1)
            return cls(p, m)
        return cls("openrouter", s)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class Result:
    run_id: str
    candidate: str
    case_id: str
    role: str
    rep: int
    ok: bool
    schema_expected: bool
    schema_valid: bool | None
    repaired: bool
    tool_expected: str | None
    tool_call_ok: bool | None
    ttft_ms: int | None
    latency_ms: int
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    cost_usd: float
    content: str
    tool_calls: list[dict[str, Any]]
    parsed: Any
    error: str | None


def _tool_ok(case: Case, tool_calls: list[dict[str, Any]]) -> bool | None:
    if case.expect_tool is None:
        return None
    for tc in tool_calls:
        if tc["name"] == case.expect_tool and INVALID_ARGS_KEY not in tc["arguments"]:
            spec = next((t for t in case.tools if t.name == tc["name"]), None)
            required = (spec.parameters.get("required") if spec else []) or []
            if all(k in tc["arguments"] for k in required):
                return True
    return False


async def run_case(client: ModelClient, cand: Candidate, case: Case, rep: int, run_id: str,
                   n_ledger_before: int, ledger_count) -> Result:
    req = ChatRequest(
        role=case.role, messages=case.messages, tools=case.tools or None, response_schema=case.schema,
        max_output_tokens=case.max_output_tokens, budget=TokenBudget(input_tokens=200_000),
        trace=TraceCtx(run_id=run_id, step=f"bakeoff:{case.id}:{rep}"),
        cache_namespace=f"bakeoff/{run_id}/{rep}",
    )
    t0 = time.perf_counter()
    ttft = None
    final = None
    error = None
    try:
        async for ev in client.stream(req):
            if ttft is None and ev.type in ("text_delta", "tool_call"):
                ttft = int((time.perf_counter() - t0) * 1000)
            if ev.type == "error":
                error = ev.error
            if ev.type == "done":
                final = ev.response
    except Exception as e:  # noqa: BLE001 - record every failure mode
        error = f"{type(e).__name__}: {e}"[:500]
    latency = int((time.perf_counter() - t0) * 1000)
    tcs = [tc.model_dump() for tc in final.tool_calls] if final else []
    calls = ledger_count() - n_ledger_before
    schema_valid = None
    if case.schema is not None and not tcs:
        schema_valid = bool(final and final.parsed is not None)
        if schema_valid and case.expect_count is not None:
            schema_valid = len(final.parsed.get("scores", [])) == case.expect_count
    u = final.usage if final else None
    return Result(
        run_id=run_id, candidate=cand.label, case_id=case.id, role=case.role, rep=rep,
        ok=final is not None and error is None, schema_expected=case.schema is not None,
        schema_valid=schema_valid, repaired=calls > 1, tool_expected=case.expect_tool,
        tool_call_ok=_tool_ok(case, tcs), ttft_ms=ttft, latency_ms=latency,
        tokens_in=u.tokens_in if u else 0, tokens_out=u.tokens_out if u else 0,
        tokens_cached=u.tokens_cached if u else 0, cost_usd=(u.cost_usd or 0.0) if u else 0.0,
        content=final.content if final else "", tool_calls=tcs, parsed=final.parsed if final else None,
        error=error,
    )


def summarize(results: list[Result]) -> list[dict[str, Any]]:
    rows = []
    for cand in sorted({r.candidate for r in results}):
        rs = [r for r in results if r.candidate == cand]
        sch = [r for r in rs if r.schema_valid is not None]
        tool = [r for r in rs if r.tool_call_ok is not None]
        ttfts = [r.ttft_ms for r in rs if r.ttft_ms is not None]
        rows.append({
            "candidate": cand,
            "n": len(rs),
            "errors": sum(not r.ok for r in rs),
            "schema_valid": round(sum(r.schema_valid for r in sch) / len(sch), 3) if sch else None,
            "schema_valid_first_try": (
                round(sum(r.schema_valid and not r.repaired for r in sch) / len(sch), 3) if sch else None
            ),
            "tool_call_ok": round(sum(r.tool_call_ok for r in tool) / len(tool), 3) if tool else None,
            "ttft_p50_ms": statistics.median(ttfts) if ttfts else None,
            "latency_p50_ms": statistics.median([r.latency_ms for r in rs]),
            "cost_total_usd": round(sum(r.cost_usd for r in rs), 6),
            "cost_per_call_usd": round(sum(r.cost_usd for r in rs) / len(rs), 6),
        })
    return rows


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no results)"
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(out)


def write_outputs(out_dir: Path, results: list[Result], meta: dict[str, Any]) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(asdict(r), default=str) + "\n")
    rows = summarize(results)
    (out_dir / "summary.json").write_text(json.dumps({"meta": meta, "summary": rows}, indent=1, default=str))
    return rows


def new_run_id(role: str) -> str:
    return f"bakeoff-{role.replace('.', '_')}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
