"""B5 naive baseline (`pipeline: naive`, TDD §19.1).

One model conversation with the `search` tool (≤ 12 searches) and the user's profile text writes the brief
directly. No planning, pool, scoring, code selection, source-pack extraction or claim check. Search-result
snippets are the only passages; citations to anything else are removed (the only code-level check).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.agents.cos.llm import (
    CallCtx,
    StructuredOutputFailure,
    chat,
    parse_structured,
    render_payload,
    system_message,
)
from app.agents.cos.packs import snippet_passage
from app.agents.cos.prompts import load_prompt
from app.agents.cos.search_loop import SEARCH_SPEC
from app.agents.cos.tooling import ToolUnavailable, render_hits, search, tool_ctx
from app.agents.cos.write import DEFAULT_OUTRO, est_duration_s
from app.contracts.agent_io import UserContext
from app.contracts.brief import BriefScript, BriefSentence, BriefStory, Passage, SourcePack
from app.contracts.common import BudgetExceeded
from app.contracts.llm import Message
from app.contracts.memory import MemorySnapshot
from app.contracts.sources import SearchHit
from app.contracts.tools import Tool


class NaiveStory(BaseModel):
    story_ref: str  # doc id of the main search result
    headline: str
    why_tag: str = "for you"


class NaiveSentence(BaseModel):
    text: str
    story_ref: str | None = None
    cites: list[str] = []
    kind: Literal["intro", "story", "transition", "outro"] = "story"


class NaiveBrief(BaseModel):
    stories: list[NaiveStory]
    sentences: list[NaiveSentence]


def _resolve_cites(cites: list[str], passages: dict[str, Passage]) -> list[str]:
    """Keep known passage ids; map a bare doc id to its snippet passage. Unknown cites are removed."""
    by_doc = {p.doc_id: p.passage_id for p in passages.values()}
    out = []
    for c in cites:
        pid = c if c in passages else by_doc.get(f"snip:{c}") or by_doc.get(c)
        if pid and pid not in out:
            out.append(pid)
    return out


def profile_text(memory: MemorySnapshot) -> str:
    if memory.profile_text:
        return memory.profile_text
    lines = [
        f"- {i.label}" + (f" ({i.entity})" if i.entity else "")
        for i in sorted(memory.interests, key=lambda i: (-i.weight, i.id))
    ]
    lines += [f"- delivery {p.key}: {p.value}" for p in memory.delivery]
    return "\n".join(lines) or "(empty profile)"


async def run_naive(
    cc: CallCtx,
    user_ctx: UserContext,
    memory: MemorySnapshot,
    tools: dict[str, Tool],
    *,
    max_searches: int = 12,
    max_rounds: int = 16,
    k_target: int = 6,
) -> tuple[BriefScript, list[SourcePack]]:
    prompt = load_prompt("cos.naive")
    hits: dict[str, SearchHit] = {}
    used = 0
    messages = [
        system_message(prompt),
        Message(
            role="user",
            content=render_payload(
                {
                    "date": user_ctx.local_date.isoformat(),
                    "profile": profile_text(memory),
                    "stories_wanted": k_target,
                    "searches_allowed": max_searches,
                }
            ),
        ),
    ]
    draft: NaiveBrief | None = None
    for _ in range(max_rounds):
        final = used >= max_searches
        try:
            resp = await chat(
                cc,
                prompt,
                messages,
                step="B5",
                tools=None if final else [SEARCH_SPEC],
                schema=NaiveBrief if final else None,
                max_output_tokens=3000,
            )
        except BudgetExceeded:
            cc.trace.degraded("B5", "token budget exceeded")
            break
        if resp.tool_calls and not final:
            messages.append(Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls))
            for call in resp.tool_calls:
                if call.name != "search" or used >= max_searches:
                    out = (
                        "Search budget exhausted. Write the brief now."
                        if call.name == "search"
                        else "Unknown tool."
                    )
                else:
                    used += 1
                    ctx = tool_ctx(
                        mode=user_ctx.mode,
                        now=user_ctx.now,
                        run_id=cc.run_id,
                        user_id=cc.user_id,
                        budget=cc.budget,
                        role=prompt.role,
                        step="B5",
                        job_id=cc.job_id,
                    )
                    try:
                        found = await search(
                            tools, ctx, str(call.arguments.get("query", "")), until=user_ctx.now
                        )
                        for h in found:
                            hits.setdefault(h.doc_id, h)
                        out = render_hits(found)
                        cc.trace.add(
                            "B5", "tool", tool="search", query=call.arguments.get("query"), n_hits=len(found)
                        )
                    except (ToolUnavailable, BudgetExceeded) as e:
                        out = f"Search failed: {e}"
                messages.append(Message(role="tool", tool_call_id=call.id, content=out))
            continue
        try:
            draft = parse_structured(resp, NaiveBrief)
            break
        except StructuredOutputFailure:
            if final:
                cc.trace.degraded("B5", "invalid final brief")
                break
            messages.append(Message(role="assistant", content=resp.content))
            messages.append(
                Message(role="user", content="Now output the final brief as JSON in the required schema.")
            )
            used = max_searches  # force the final structured turn

    passages: dict[str, Passage] = {}
    for h in hits.values():
        p = snippet_passage(h)
        if p:
            passages[p.passage_id] = p
    stories: list[BriefStory] = []
    sentences: list[BriefSentence] = []
    packs: list[SourcePack] = []
    if draft:
        for st in draft.stories:
            stories.append(
                BriefStory(
                    story_ref=st.story_ref,
                    slot="for_you",
                    headline=st.headline,
                    why_tag=st.why_tag or "for you",
                )
            )
        for s in draft.sentences:
            if s.text.strip():
                sentences.append(
                    BriefSentence(
                        sid=f"s{len(sentences):03d}",
                        text=s.text.strip(),
                        story_ref=s.story_ref,
                        cites=_resolve_cites(s.cites, passages),
                        kind=s.kind,
                    )
                )
        for st in stories:
            cited = {c for s in sentences if s.story_ref == st.story_ref for c in s.cites}
            own = [
                p for p in passages.values() if p.passage_id in cited or p.doc_id == f"snip:{st.story_ref}"
            ]
            packs.append(
                SourcePack(
                    story_ref=st.story_ref, headline=st.headline, facts=[], passages=own, depth="light"
                )
            )
    if not sentences or sentences[-1].kind != "outro":
        sentences.append(BriefSentence(sid=f"s{len(sentences):03d}", text=DEFAULT_OUTRO, kind="outro"))
    script = BriefScript(
        brief_id="",
        user_id=user_ctx.user_id,
        local_date=user_ctx.local_date,
        arm=user_ctx.arm,
        stories=stories,
        sentences=sentences,
        est_duration_s=est_duration_s(sentences),
    )
    return script, packs
