"""Follow-up research (TDD §14): job `followup_research(question_id)` → `FollowupAnswer`.

Bounded loop on role `followup.research` with `search` (≤ 8) and `fetch` (≤ 6) tools. Fetched documents go
through the same map-reduce extraction as C6 (`cos.extract_passages`), so the model only ever sees and cites
verified verbatim passages (or verbatim search snippets). Then a final structured answer, a code-level
citation check, and the C8 claim check. With no supported sentence left the answer is `unanswerable`,
with what was found.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.agents.cos.claim_check import ToCheck, check_claims
from app.agents.cos.config import cos_config
from app.agents.cos.llm import (
    CallCtx,
    StructuredOutputFailure,
    chat,
    parse_structured,
    render_payload,
    system_message,
)
from app.agents.cos.packs import CHARS_PER_TOKEN, extract_document, snippet_passage
from app.agents.cos.prompts import load_prompt
from app.agents.cos.search_loop import SEARCH_SPEC
from app.agents.cos.tooling import ToolUnavailable, fetch, render_hits, search, tool_ctx
from app.agents.cos.trace import RunTrace
from app.agents.cos.untrusted import sanitize, wrap
from app.config.loader import ExperimentCfg, ExperimentFlags
from app.contracts.agent_io import AnswerSentence, FollowupAnswer, OpenQuestion, UserContext
from app.contracts.brief import Passage
from app.contracts.common import BudgetExceeded, TokenBudget
from app.contracts.llm import Message, ModelClient
from app.contracts.sources import SearchHit
from app.contracts.tools import Tool, ToolSpec

FETCH_SPEC = ToolSpec(
    name="fetch",
    description="Read a document from the search results. Returns verbatim key passages with passage ids.",
    parameters={"type": "object", "properties": {"doc_id": {"type": "string"}, "url": {"type": "string"}}},
)
PASSAGE_CHARS = 1500 * CHARS_PER_TOKEN


class FollowupDraft(BaseModel):
    status: Literal["answered", "unanswerable"]
    answer_sentences: list[AnswerSentence] = []
    what_was_found: str = ""


async def run_followup(
    question: OpenQuestion,
    *,
    user_ctx: UserContext,
    model_client: ModelClient,
    tools: dict[str, Tool],
    flags: ExperimentCfg | ExperimentFlags | str | None = None,
    agent_cfg: dict | None = None,
    max_rounds: int = 12,
) -> tuple[FollowupAnswer, RunTrace]:
    cfg = cos_config(flags, agent_cfg)
    fb = cfg.followup_budget
    trace = RunTrace(run_id=user_ctx.run_id, user_id=user_ctx.user_id, job="followup_research", arm=cfg.arm)
    budget = TokenBudget(input_tokens=fb.input_tokens, searches=fb.searches, fetches=fb.fetches)
    cc = CallCtx(
        client=model_client,
        budget=budget,
        trace=trace,
        run_id=user_ctx.run_id,
        user_id=user_ctx.user_id,
        job_id=user_ctx.job_id,
        cache_namespace=user_ctx.run_id,
    )
    prompt = load_prompt("followup.research")
    hits: dict[str, SearchHit] = {}
    passages: dict[str, Passage] = {}
    used = {"search": 0, "fetch": 0}
    caps = {"search": fb.searches, "fetch": fb.fetches}

    def ctx():
        return tool_ctx(
            mode=user_ctx.mode,
            now=user_ctx.now,
            run_id=cc.run_id,
            user_id=cc.user_id,
            budget=budget,
            role=prompt.role,
            step="F",
            job_id=cc.job_id,
        )

    async def do_search(args: dict) -> str:
        q = str(args.get("query", "")).strip()
        if not q:
            return "Empty query."
        found = await search(tools, ctx(), q, until=user_ctx.now)
        out = []
        for h in found:
            hits.setdefault(h.doc_id, h)
            sp = snippet_passage(h)
            if sp:
                passages[sp.passage_id] = sp
                out.append(f"(snippet passage id: {sp.passage_id})")
            out.append(render_hits([h]))
        trace.add("F", "tool", tool="search", query=q, n_hits=len(found))
        return "\n".join(out) or "(no results)"

    async def do_fetch(args: dict) -> str:
        doc_id = args.get("doc_id")
        url = args.get("url") or (hits[doc_id].url if doc_id in hits else None)
        doc = await fetch(tools, ctx(), url=url, doc_id=doc_id, max_tokens=1500)
        if doc is None or not doc.text.strip():
            return "Document unavailable."
        ps, _, _ = await extract_document(
            cc, doc, max_chars=PASSAGE_CHARS, step="F.extract", question=question.question
        )
        for p in ps:
            passages[p.passage_id] = p
        trace.add("F", "tool", tool="fetch", doc_id=doc.url_hash, n_passages=len(ps))
        return (
            "\n".join(wrap(p.text, id=p.passage_id, source=p.source) for p in ps) or "(no relevant passages)"
        )

    handlers = {"search": do_search, "fetch": do_fetch}
    messages = [
        system_message(prompt),
        Message(
            role="user",
            content=render_payload(
                {
                    "question": sanitize(question.question),
                    "context": question.context,
                    "asked_at": question.created_at,
                    "today": user_ctx.local_date,
                    "limits": caps,
                }
            ),
        ),
    ]
    for _ in range(max_rounds):
        offered = [
            s for s, n in ((SEARCH_SPEC, "search"), (FETCH_SPEC, "fetch")) if used[n] < caps[n] and n in tools
        ]
        if not offered:
            break
        try:
            resp = await chat(cc, prompt, messages, step="F", tools=offered, max_output_tokens=800)
        except BudgetExceeded:
            trace.degraded("F", "token budget exceeded during research")
            break
        if not resp.tool_calls:
            break
        messages.append(Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            if call.name not in handlers:
                out = f"Unknown tool {call.name}."
            elif used[call.name] >= caps[call.name]:
                out = f"{call.name} budget exhausted."
            else:
                used[call.name] += 1
                try:
                    out = await handlers[call.name](call.arguments)
                except (ToolUnavailable, BudgetExceeded) as e:
                    out = f"{call.name} failed: {e}"
                    trace.add("F", "tool", ok=False, tool=call.name, error=str(e)[:200])
            messages.append(Message(role="tool", tool_call_id=call.id, content=out))

    draft = await _finalize(cc, prompt, messages, passages)
    sentences = []
    for s in draft.answer_sentences:
        cites = [c for c in dict.fromkeys(s.cites) if c in passages]
        if s.text.strip() and cites:
            sentences.append(AnswerSentence(text=s.text.strip(), cites=cites))
    if sentences and cfg.flags.claim_check:
        outcomes = await check_claims(
            cc,
            [ToCheck(f"a{n}", s.text, s.cites) for n, s in enumerate(sentences)],
            passages,
            step="F.claim_check",
        )
        sentences = [
            AnswerSentence(text=o.text, cites=s.cites)
            for o, s in zip(outcomes, sentences, strict=True)
            if o.text is not None
        ]
    answered = draft.status == "answered" and bool(sentences)
    cited = {c for s in sentences for c in s.cites}
    what = draft.what_was_found or (
        "; ".join(h.title for h in list(hits.values())[:5]) if hits else "No relevant sources were found."
    )
    ans = FollowupAnswer(
        question_id=question.id,
        status="answered" if answered else "unanswerable",
        answer_sentences=sentences if answered else [],
        what_was_found=what,
        passages=[p for pid, p in passages.items() if pid in cited] if answered else [],
        researched_at=user_ctx.now,
    )
    trace.add(
        "F",
        "code",
        status=ans.status,
        searches=used["search"],
        fetches=used["fetch"],
        used_input_tokens=budget.used_input_tokens,
    )
    return ans, trace


async def _finalize(
    cc: CallCtx, prompt, messages: list[Message], passages: dict[str, Passage]
) -> FollowupDraft:
    final = messages + [
        Message(
            role="user",
            content=(
                "Stop researching. Answer now in the required JSON schema. Cite only passage ids shown above "
                f"({len(passages)} available). If the sources do not answer the question, return "
                "status 'unanswerable' and say what was found."
            ),
        )
    ]
    try:
        resp = await chat(cc, prompt, final, step="F.answer", schema=FollowupDraft, max_output_tokens=1200)
        return parse_structured(resp, FollowupDraft)
    except (BudgetExceeded, StructuredOutputFailure) as e:
        cc.trace.degraded("F.answer", f"{type(e).__name__}: unanswerable")
        return FollowupDraft(status="unanswerable", what_was_found="")
