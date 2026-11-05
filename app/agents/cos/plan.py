"""C1 plan (`cos.plan`): memory → personal queries + adjacent-discovery queries."""

from __future__ import annotations

from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.prompts import load_prompt
from app.contracts.agent_io import OpenQuestion, ShownStory
from app.contracts.brief import SearchPlan, SearchQuery
from app.contracts.common import BudgetExceeded
from app.contracts.memory import MemorySnapshot

MAX_PERSONAL = 6
MAX_ADJACENT = 4


def memory_summary(memory: MemorySnapshot, limit: int = 25) -> list[dict]:
    ranked = sorted(memory.interests, key=lambda i: (-i.explicit_pin, -i.weight, i.id))[:limit]
    return [
        {
            "id": i.id,
            "label": i.label,
            "subtopic": i.subtopic,
            "entity": i.entity,
            "kind": i.kind,
            "weight": round(i.weight, 3),
            "pinned": i.explicit_pin,
        }
        for i in ranked
    ]


def normalise_plan(plan: SearchPlan, known_ids: set[str]) -> SearchPlan:
    """Enforce caps (≤6 personal, ≤4 adjacent), drop duplicates, keep only known memory refs."""
    seen: set[str] = set()
    counts = {"personal": 0, "adjacent": 0}
    caps = {"personal": MAX_PERSONAL, "adjacent": MAX_ADJACENT}
    out = []
    for q in plan.queries:
        key = " ".join(q.query.lower().split())
        if not key or key in seen or counts[q.kind] >= caps[q.kind]:
            continue
        seen.add(key)
        counts[q.kind] += 1
        out.append(q.model_copy(update={"memory_refs": [r for r in q.memory_refs if r in known_ids]}))
    return SearchPlan(queries=out)


def fallback_plan(memory: MemorySnapshot, open_questions: list[OpenQuestion]) -> SearchPlan:
    """Deterministic plan used when the model call fails: top interests and open questions, no adjacent."""
    queries: list[SearchQuery] = []
    for q in open_questions:
        if q.status in ("open", "researching"):
            queries.append(SearchQuery(query=q.question[:200], kind="personal", rationale="open question"))
    ranked = sorted(
        memory.interests, key=lambda i: (i.kind != "keep_posted", -i.explicit_pin, -i.weight, i.id)
    )
    for i in ranked:
        queries.append(
            SearchQuery(
                query=i.entity or i.label, kind="personal", rationale="top interest", memory_refs=[i.id]
            )
        )
    return normalise_plan(SearchPlan(queries=queries[:10]), {i.id for i in memory.interests})


async def plan_searches(
    cc: CallCtx,
    memory: MemorySnapshot,
    open_questions: list[OpenQuestion],
    history: list[ShownStory],
    followed_beats: list[str],
) -> SearchPlan:
    prompt = load_prompt("cos.plan")
    payload = {
        "interests": memory_summary(memory),
        "keep_me_posted": [i.label for i in memory.interests if i.kind == "keep_posted"],
        "open_questions": [q.question for q in open_questions if q.status in ("open", "researching")],
        "followed_beats": followed_beats,
        "recently_shown_headlines": [h.headline for h in history[-20:]],
        "caps": {"personal": MAX_PERSONAL, "adjacent": MAX_ADJACENT},
    }
    try:
        plan = await call_structured(cc, prompt, payload, SearchPlan, step="C1")
    except (BudgetExceeded, StructuredOutputFailure) as e:
        cc.trace.degraded("C1", f"{type(e).__name__}: fallback plan from memory")
        return fallback_plan(memory, open_questions)
    plan = normalise_plan(plan, {i.id for i in memory.interests})
    if not plan.queries:
        cc.trace.degraded("C1", "empty plan: fallback plan from memory")
        return fallback_plan(memory, open_questions)
    return plan
