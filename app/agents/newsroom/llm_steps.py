"""Model-backed steps: N3 cluster tiebreak and N5 scoring (with bounded verification fetches).

All model calls go through `app.contracts.llm.ModelClient`. The token budget is enforced by the
model client (it raises `BudgetExceeded`); this module also skips a call up front when its
estimated input would not fit, and degrades to rules-only entries instead of failing the run.
Fetches are charged here (`TokenBudget.charge_fetch`) before the tool is invoked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.newsroom.clustering import TiebreakPair
from app.agents.newsroom.features import SourceIndex, rules_evidence
from app.agents.newsroom.normalize import canonical_url, strip_invisible
from app.agents.newsroom.prompts import Prompt
from app.agents.newsroom.stories import StoryDraft
from app.agents.newsroom.trace import LlmCallRecord, NewsroomTrace, ToolCallRecord
from app.contracts.common import BudgetExceeded, Mode, Strict, TokenBudget, TraceCtx
from app.contracts.digest import DigestEntryLLM, StoryFeatures
from app.contracts.llm import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelClient,
    StructuredOutputInvalid,
    ToolCall,
)
from app.contracts.sources import Document
from app.contracts.tools import FetchResult, Tool, ToolContext, ToolResult, ToolSpec

TIEBREAK_ROLE = "newsroom.cluster_tiebreak"
SCORE_ROLE = "newsroom.score"


# ---- structured outputs ---------------------------------------------------------------------------


class TiebreakDecision(Strict):
    pair_id: str
    same_event: bool
    reason: str = ""


class TiebreakOut(Strict):
    decisions: list[TiebreakDecision]


class ScoreEntryOut(BaseModel):
    """What newsroom.score writes per story (prompt v2): only the fields code reads downstream.

    `is_update_of` is code-owned (story identity) and `notes_for_editor` is only ever set by code, so
    neither is asked of the model. Unknown keys (e.g. those two, from v1-style outputs) are ignored
    rather than failing the whole batch.
    """

    model_config = ConfigDict(extra="ignore")

    story_id: str
    headline: str
    summary: str  # one sentence
    subtopics: list[str]
    entities: list[str]
    importance: int = Field(ge=0, le=100)
    evidence: list[str]  # 1-2 short strings naming features / sources


class ScoreBatchOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entries: list[ScoreEntryOut]


FETCH_SPEC = ToolSpec(
    name="fetch",
    description=(
        "Fetch the text of one article listed in this batch, to verify an ambiguous or high-stakes "
        "story. Only URLs shown in the batch are allowed. Limited budget per run."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "A URL shown in this batch."}},
        "required": ["url"],
        "additionalProperties": False,
    },
)


# ---- helpers --------------------------------------------------------------------------------------

_TAG = re.compile(r"<\s*/?\s*untrusted_source", re.I)


def wrap_untrusted(source_id: str, url: str, body: str) -> str:
    """TDD §5.3 wrapper. Tag look-alikes inside the body are defanged so content can't close it."""
    body = _TAG.sub("[untrusted_source", strip_invisible(body))
    url = url.replace('"', "%22")
    return f'<untrusted_source id="{source_id}" url="{url}">\n{body}\n</untrusted_source>'


def estimate_tokens(messages: Sequence[Message]) -> int:
    return sum(len(m.content) for m in messages) // 4 + 16 * len(messages)


def _parse(resp: ChatResponse, schema: type[BaseModel]) -> tuple[dict[str, Any] | None, str | None]:
    raw: Any = resp.parsed
    if raw is None and resp.content:
        text = resp.content.strip()
        m = re.search(r"\{.*\}", text, re.S)
        try:
            raw = json.loads(m.group(0) if m else text)
        except (json.JSONDecodeError, AttributeError):
            return None, "output is not JSON"
    if not isinstance(raw, dict):
        return None, "no structured output"
    return raw, None


@dataclass
class RunCtx:
    """Per-run context shared by the model-backed steps. Carries no user data (NEWS-2)."""

    run_id: str
    beat: str
    at: datetime
    mode: Mode
    budget: TokenBudget
    trace: NewsroomTrace
    cache_namespace: str
    job_id: str | None = None

    def trace_ctx(self, role: str, step: str) -> TraceCtx:
        return TraceCtx(run_id=self.run_id, user_id=None, job_id=self.job_id, role=role, step=step)

    def tool_ctx(self, step: str) -> ToolContext:
        return ToolContext(
            mode=self.mode,
            now=self.at,
            run_id=self.run_id,
            user_id=None,
            budget=self.budget,
            trace=self.trace_ctx("tool.fetch", step),
            budget_precharged=True,  # _fetch charges budget.charge_fetch() before calling the tool
        )


def _record(
    step: str,
    prompt: Prompt,
    resp: ChatResponse | None,
    *,
    ok: bool,
    error: str | None = None,
    output: dict | None = None,
) -> LlmCallRecord:
    return LlmCallRecord(
        step=step,
        role=prompt.role,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.sha256,
        model=resp.model if resp else None,
        provider=resp.provider if resp else None,
        tokens_in=resp.usage.tokens_in if resp else 0,
        tokens_out=resp.usage.tokens_out if resp else 0,
        tokens_cached=resp.usage.tokens_cached if resp else 0,
        cost_usd=resp.usage.cost_usd if resp else None,
        cached=resp.cached if resp else False,
        latency_ms=resp.latency_ms if resp else 0,
        ok=ok,
        error=error,
        output=output,
    )


# ---- N3 tiebreak ----------------------------------------------------------------------------------


class LlmTiebreaker:
    """`clustering.Tiebreaker` backed by role newsroom.cluster_tiebreak (batched yes/no)."""

    def __init__(
        self,
        client: ModelClient,
        prompt: Prompt,
        ctx: RunCtx,
        *,
        batch_size: int = 20,
        max_output_tokens: int = 1500,
    ) -> None:
        self.client, self.prompt, self.ctx = client, prompt, ctx
        self.batch_size, self.max_output_tokens = batch_size, max_output_tokens

    async def __call__(self, pairs: list[TiebreakPair]) -> dict[str, bool]:
        out: dict[str, bool] = {}
        system = Message(role="system", content=self.prompt.render(), cache_breakpoint=True)
        for start in range(0, len(pairs), self.batch_size):
            batch = pairs[start : start + self.batch_size]
            body = "\n\n".join(
                f'<pair pair_id="{p.pair_id}" similarity="{p.similarity}">\n'
                f"A:\n{wrap_untrusted(p.a_id, '', p.a_text)}\n"
                f"B:\n{wrap_untrusted(p.b_id, '', p.b_text)}\n</pair>"
                for p in batch
            )
            msgs = [system, Message(role="user", content=f"Pairs ({len(batch)}):\n\n{body}")]
            if self.ctx.budget.used_input_tokens + estimate_tokens(msgs) > self.ctx.budget.input_tokens:
                self.ctx.trace.degraded.append(f"N3 tiebreak: skipped {len(batch)} pairs (token budget)")
                continue
            req = ChatRequest(
                role=TIEBREAK_ROLE,
                messages=msgs,
                response_schema=TiebreakOut,
                max_output_tokens=self.max_output_tokens,
                temperature=0.0,
                budget=self.ctx.budget,
                trace=self.ctx.trace_ctx(TIEBREAK_ROLE, "N3"),
                cache_namespace=self.ctx.cache_namespace,
            )
            try:
                resp = await self.client.chat(req)
            except BudgetExceeded as e:
                self.ctx.trace.add_llm(_record("N3", self.prompt, None, ok=False, error=f"budget: {e}"))
                self.ctx.trace.degraded.append(f"N3 tiebreak: {len(batch)} pairs unresolved (budget)")
                continue
            except StructuredOutputInvalid as e:
                self.ctx.trace.add_llm(_record("N3", self.prompt, None, ok=False, error=str(e)[:300]))
                self.ctx.trace.degraded.append(f"N3 tiebreak: {len(batch)} pairs unresolved (invalid output)")
                continue
            raw, err = _parse(resp, TiebreakOut)
            ok_ids = {p.pair_id for p in batch}
            if raw is not None:
                for d in raw.get("decisions", []) or []:
                    try:
                        dec = TiebreakDecision.model_validate(d)
                    except ValidationError:
                        continue
                    if dec.pair_id in ok_ids:
                        out.setdefault(dec.pair_id, dec.same_event)
            self.ctx.trace.add_llm(_record("N3", self.prompt, resp, ok=err is None, error=err, output=raw))
        return out


# ---- N5 score -------------------------------------------------------------------------------------


@dataclass
class BatchLog:
    """Trace records of one N5 batch, kept apart so concurrent batches merge in batch order."""

    llm_calls: list[LlmCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    fetches: int = 0

    def add_llm(self, rec: LlmCallRecord) -> None:
        self.llm_calls.append(rec)

    def merge_into(self, trace: NewsroomTrace) -> None:
        for rec in self.llm_calls:
            trace.add_llm(rec)
        trace.tool_calls.extend(self.tool_calls)
        trace.degraded.extend(self.degraded)
        trace.usage.fetches += self.fetches


def split_quota(total: int, n: int) -> list[int]:
    """Split `total` (e.g. fetches) over `n` batches deterministically: earlier batches get the remainder."""
    if n <= 0:
        return []
    base, rem = divmod(max(0, total), n)
    return [base + (1 if i < rem else 0) for i in range(n)]


_EVIDENCE_VOCAB = (
    "domain",
    "outlet",
    "independent",
    "rank",
    "front",
    "tier",
    "velocity",
    "new item",
    "items",
    "primary",
    "source",
    "coverage",
    "update",
    "fetch",
    "first_seen",
    "first seen",
    "n_items",
)
_HN_VOCAB = ("points", "comments", "hn", "hacker news")


def valid_evidence(
    evidence: Sequence[str], story_sources: Sequence[str], domains: Sequence[str], *, hn_signal: bool
) -> list[str]:
    """Keep evidence strings that reference a feature or a source of this story (NEWS-6)."""
    vocab = list(_EVIDENCE_VOCAB) + [s.lower() for s in story_sources] + [d.lower() for d in domains]
    if hn_signal:
        vocab += _HN_VOCAB
    out = []
    for e in evidence:
        el = e.lower()
        if (
            not hn_signal
            and any(h in el for h in _HN_VOCAB)
            and not any(s.lower() in el for s in story_sources)
        ):
            continue
        if any(v in el for v in vocab):
            out.append(e)
    return out


def fallback_entry(d: StoryDraft, features: StoryFeatures, rules: float, reason: str) -> DigestEntryLLM:
    snippet = next((it.snippet for it in d.items if it.snippet), "")
    summary = (snippet.split(". ")[0].rstrip(".") + ".") if snippet else d.title
    return DigestEntryLLM(
        story_id=d.story_id,
        headline=d.title,
        summary=summary,
        subtopics=["other"],
        entities=[],
        importance=max(0, min(100, round(rules))),
        evidence=rules_evidence(features, sorted({s for it in d.items for s in it.source_ids})),
        is_update_of=d.update_of,
        notes_for_editor=f"rules-only fallback: {reason}",
    )


def render_story(
    d: StoryDraft, features: StoryFeatures, sources: SourceIndex, *, hn_signal: bool, max_items: int = 6
) -> str:
    feats = features.model_dump(mode="json")
    if not hn_signal:
        feats.pop("hn_points_at_observation", None)
        feats.pop("comments_at_observation", None)
    feats["update_of"] = d.update_of
    src_lines = []
    for sid in sorted({s for it in d.items for s in it.source_ids}):
        obs = [o for it in d.items for o in it.observations if o.source_id == sid]
        ranks = [o.rank for o in obs if o.rank is not None]
        parts = [f"tier {sources.tier(sid)}" if sources.tier(sid) else "tier ?"]
        if ranks and sources.is_frontpage(sid):
            parts.append(f"best rank {min(ranks)}")
        if hn_signal and sources.is_hn(sid):
            pts = [o.engagement.hn_points for o in obs if o.engagement and o.engagement.hn_points is not None]
            cms = [o.engagement.comments for o in obs if o.engagement and o.engagement.comments is not None]
            if pts:
                parts.append(f"{max(pts)} points at observation")
            if cms:
                parts.append(f"{max(cms)} comments at observation")
        src_lines.append(f"- {sid} ({', '.join(parts)})")
    items_txt = []
    for n, it in enumerate(d.items[:max_items], 1):
        body = f"title: {it.title}\nsnippet: {it.snippet[:300]}" if it.snippet else f"title: {it.title}"
        items_txt.append(wrap_untrusted(f"{d.story_id}#{n}", it.url, body))
    more = f"\n(+{len(d.items) - max_items} more items)" if len(d.items) > max_items else ""
    return (
        f'<story story_id="{d.story_id}">\nfeatures: {json.dumps(feats, sort_keys=True)}\n'
        f"sources:\n" + "\n".join(src_lines) + "\nitems:\n" + "\n".join(items_txt) + more + "\n</story>"
    )


class Scorer:
    """N5: score story batches with role newsroom.score; bounded `fetch` tool use."""

    def __init__(
        self,
        client: ModelClient,
        prompt: Prompt,
        ctx: RunCtx,
        *,
        system_text: str,
        sources: SourceIndex,
        beat_subtopics: Sequence[str],
        hn_signal: bool,
        fetch_tool: Tool | None,
        fetch_max_tokens: int,
        max_output_tokens: int,
    ) -> None:
        self.client, self.prompt, self.ctx = client, prompt, ctx
        self.system_text, self.sources, self.hn_signal = system_text, sources, hn_signal
        self.allowed_subtopics = set(beat_subtopics) | {"other"}
        self.fetch_tool, self.fetch_max_tokens = fetch_tool, fetch_max_tokens
        self.max_output_tokens = max_output_tokens

    def _fetches_left(self) -> int:
        b = self.ctx.budget
        return 0 if b.fetches is None else max(0, b.fetches - b.used_fetches)

    async def _fetch(self, call: ToolCall, allowed: set[str], log: BatchLog | None = None) -> str:
        log = log if log is not None else BatchLog()
        url = str(call.arguments.get("url") or "")
        args = {"url": url}
        rec = ToolCallRecord(step="N5", tool="fetch", arguments=args, ok=False)
        try:
            if call.name != "fetch" or self.fetch_tool is None:
                rec.error = f"tool {call.name!r} is not available"
            elif not url or canonical_url(url) not in allowed:
                rec.error = "url is not one of the URLs in this batch"
            else:
                self.ctx.budget.charge_fetch()
                log.fetches += 1
                result: ToolResult = await self.fetch_tool(
                    self.ctx.tool_ctx("N5"), url=url, max_tokens=self.fetch_max_tokens
                )
                if not result.ok:
                    rec.error = result.error or "fetch failed"
                else:
                    rec.ok = True
                    rec.result_excerpt = self._render_doc(url, result.data)
        except BudgetExceeded:
            rec.error = "fetch budget exhausted for this run"
        except Exception as e:  # a tool failure must not fail the run
            rec.error = f"fetch error: {type(e).__name__}: {e}"
        log.tool_calls.append(rec)
        return rec.result_excerpt if rec.ok and rec.result_excerpt else f"error: {rec.error}"

    def _render_doc(self, url: str, data: Any) -> str:
        doc: Document | None = None
        if isinstance(data, FetchResult):
            doc = data.document
        elif isinstance(data, Document):
            doc = data
        elif isinstance(data, Mapping):
            raw = data.get("document", data)
            try:
                doc = Document.model_validate(raw) if raw else None
            except ValidationError:
                doc = None
            if doc is None and raw:
                text = str(raw.get("text", "")) if isinstance(raw, Mapping) else str(raw)
                return wrap_untrusted("fetch", url, text[: self.fetch_max_tokens * 4])
        if doc is None:
            return wrap_untrusted("fetch", url, "(no document)")
        text = doc.text[: self.fetch_max_tokens * 4]
        body = f"title: {doc.title or ''}\npublished_at: {doc.published_at}\n\n{text}"
        return wrap_untrusted(doc.url_hash, doc.url, body)

    def estimate_batch(self, drafts: Sequence[StoryDraft], features: Mapping[str, StoryFeatures]) -> int:
        """Estimated input tokens of a batch's first call (same estimate as the budget pre-check)."""
        stories_txt = "\n\n".join(
            render_story(d, features[d.story_id], self.sources, hn_signal=self.hn_signal) for d in drafts
        )
        header = "x" * 200  # run time, beat, batch number and fetch line
        msgs = [
            Message(role="system", content=self.system_text),
            Message(role="user", content=header + stories_txt),
        ]
        return estimate_tokens(msgs)

    async def score_batch(
        self,
        drafts: Sequence[StoryDraft],
        features: Mapping[str, StoryFeatures],
        rules: Mapping[str, float],
        *,
        batch_no: int,
        n_batches: int,
        fetch_quota: int | None = None,
        tokens_before: int | None = None,
        log: BatchLog | None = None,
    ) -> list[DigestEntryLLM]:
        """Score one batch.

        Called alone (defaults) it behaves as the sequential loop: fetches left and token usage are read
        from the shared run budget, and trace records are written straight to the run trace.
        For concurrent batches the caller passes, per batch, a deterministic `fetch_quota` (this batch's
        share of the run's fetches), `tokens_before` (input tokens used before N5 plus the estimated
        input of the batches before this one; the budget pre-check then counts only those and this
        batch's own calls, instead of the live counter that concurrent batches race on) and a `log` that
        it merges into the trace in batch order afterwards.
        """
        own_log = log is None
        log = BatchLog() if log is None else log
        fetches_at_start = log.fetches

        def fetches_left() -> int:
            left = self._fetches_left()
            if fetch_quota is None:
                return left
            return max(0, min(left, fetch_quota - (log.fetches - fetches_at_start)))

        def tokens_used() -> int:
            if tokens_before is None:
                return self.ctx.budget.used_input_tokens
            return tokens_before + sum(r.tokens_in for r in log.llm_calls)

        stories_txt = "\n\n".join(
            render_story(d, features[d.story_id], self.sources, hn_signal=self.hn_signal) for d in drafts
        )
        allowed = {it.url for d in drafts for it in d.items}
        # Same wording with or without a quota, so the request (and its cache key) does not depend on
        # the concurrency setting whenever the numbers agree.
        header = (
            f"Run time: {self.ctx.at.isoformat()}. Beat: {self.ctx.beat}. Batch {batch_no} of {n_batches}. "
            f"Verification fetches available for this batch: {fetches_left()}.\n"
            f"Score each of these {len(drafts)} stories.\n\n"
        )
        msgs = [
            Message(role="system", content=self.system_text, cache_breakpoint=True),
            Message(role="user", content=header + stories_txt),
        ]
        offer_tools = self.fetch_tool is not None and fetches_left() > 0
        raw: dict | None = None
        reason = "no valid output"
        for _round in range(fetches_left() + 2):
            if tokens_used() + estimate_tokens(msgs) > self.ctx.budget.input_tokens:
                reason = "token budget"
                log.degraded.append(f"N5 batch {batch_no}: skipped (token budget)")
                break
            req = ChatRequest(
                role=SCORE_ROLE,
                messages=list(msgs),
                tools=[FETCH_SPEC] if offer_tools else None,
                response_schema=ScoreBatchOut,
                max_output_tokens=self.max_output_tokens,
                temperature=0.0,
                budget=self.ctx.budget,
                trace=self.ctx.trace_ctx(SCORE_ROLE, "N5"),
                cache_namespace=self.ctx.cache_namespace,
            )
            try:
                resp = await self.client.chat(req)
            except BudgetExceeded as e:
                log.add_llm(_record("N5", self.prompt, None, ok=False, error=f"budget: {e}"))
                log.degraded.append(f"N5 batch {batch_no}: budget exceeded")
                reason = "token budget"
                break
            except StructuredOutputInvalid as e:
                log.add_llm(_record("N5", self.prompt, None, ok=False, error=str(e)[:300]))
                log.degraded.append(f"N5 batch {batch_no}: invalid structured output")
                reason = "invalid structured output"
                break
            if resp.tool_calls and offer_tools:
                log.add_llm(
                    _record(
                        "N5",
                        self.prompt,
                        resp,
                        ok=True,
                        output={"tool_calls": [tc.model_dump() for tc in resp.tool_calls]},
                    )
                )
                msgs.append(Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls))
                for tc in resp.tool_calls:
                    if fetch_quota is not None and fetches_left() <= 0 and tc.name == "fetch":
                        err = "fetch budget exhausted for this batch"
                        log.tool_calls.append(
                            ToolCallRecord(
                                step="N5",
                                tool="fetch",
                                arguments={"url": str(tc.arguments.get("url") or "")},
                                ok=False,
                                error=err,
                            )
                        )
                        result_text = f"error: {err}"
                    else:
                        result_text = await self._fetch(tc, allowed, log)
                    msgs.append(Message(role="tool", tool_call_id=tc.id, content=result_text))
                offer_tools = fetches_left() > 0
                continue
            raw, err = _parse(resp, ScoreBatchOut)
            log.add_llm(_record("N5", self.prompt, resp, ok=err is None, error=err, output=raw))
            if err:
                reason = err
            break
        out = self._finalize(drafts, features, rules, raw, reason, batch_no, log)
        if own_log:
            log.merge_into(self.ctx.trace)
        return out


    def _finalize(self, drafts, features, rules, raw, reason, batch_no, log=None) -> list[DigestEntryLLM]:
        by_id = {d.story_id: d for d in drafts}
        got: dict[str, DigestEntryLLM] = {}
        for e in (raw or {}).get("entries", []) or []:
            if not isinstance(e, Mapping) or e.get("story_id") not in by_id or e["story_id"] in got:
                continue
            e = dict(e)
            try:
                e["importance"] = max(0, min(100, int(round(float(e.get("importance", 0))))))
                entry = DigestEntryLLM.model_validate(e)
            except (ValidationError, TypeError, ValueError):
                continue
            d = by_id[entry.story_id]
            f = features[d.story_id]
            notes = [entry.notes_for_editor] if entry.notes_for_editor else []
            subs = [s for s in entry.subtopics if s in self.allowed_subtopics] or ["other"]
            ev = valid_evidence(
                entry.evidence,
                sorted({s for it in d.items for s in it.source_ids}),
                sorted({it.domain for it in d.items}),
                hn_signal=self.hn_signal,
            )
            importance = entry.importance
            if not ev:
                importance = min(importance, round(rules[d.story_id]))
                ev = rules_evidence(f, sorted({s for it in d.items for s in it.source_ids}))
                notes.append("no feature/source evidence given; importance capped at rules score")
            got[entry.story_id] = entry.model_copy(
                update={
                    "subtopics": subs,
                    "evidence": ev,
                    "importance": importance,
                    "is_update_of": d.update_of,  # code-owned (story identity)
                    "notes_for_editor": "; ".join(notes) or None,
                }
            )
        out = []
        missing = 0
        for d in drafts:
            if d.story_id in got:
                out.append(got[d.story_id])
            else:
                missing += 1
                out.append(fallback_entry(d, features[d.story_id], rules[d.story_id], reason))
        if missing:
            note = f"N5 batch {batch_no}: {missing} stories rules-only ({reason})"
            (log.degraded if log is not None else self.ctx.trace.degraded).append(note)
        return out
