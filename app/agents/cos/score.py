"""C4 score (`cos.score`, structured, batched ~20 per call) → `CandidateScores` per pool item.

Post-processing (code): clamp to the contract ranges, keep only known memory ids in `because`, and for
digest candidates use the newsroom's importance (unless A1 `newsroom_separation=false`, where the chief
of staff judges importance itself). A failed batch falls back to a deterministic heuristic score.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.plan import memory_summary
from app.agents.cos.pool import PoolItem
from app.agents.cos.prompts import load_prompt
from app.agents.cos.text import jaccard, tokens
from app.agents.cos.untrusted import wrap
from app.contracts.agent_io import ShownStory
from app.contracts.brief import CandidateScores
from app.contracts.common import BudgetExceeded
from app.contracts.memory import MemorySnapshot

DEFAULT_IMPORTANCE = 40


class ScoreBatch(BaseModel):
    scores: list[CandidateScores]


def heuristic_score(item: PoolItem, memory: MemorySnapshot, history: list[ShownStory]) -> CandidateScores:
    """Deterministic fallback: subtopic/entity overlap with memory, novelty vs. history headlines."""
    ht = tokens(item.candidate.headline + " " + " ".join(item.entities))
    wmax = max((i.weight for i in memory.interests), default=1.0) or 1.0
    rel, because = 0.0, []
    for i in sorted(memory.interests, key=lambda i: i.id):
        m = 1.0 if i.subtopic in item.subtopics else 0.0
        if (i.entity and i.entity.lower() in {e.lower() for e in item.entities}) or (tokens(i.label) & ht):
            m = min(1.0, m + 0.5)
        if m > 0:
            because.append(i.id)
            rel = max(rel, m * (0.5 + 0.5 * min(1.0, i.weight / wmax)))
    novelty = 1.0 - max((jaccard(ht, tokens(h.headline)) for h in history), default=0.0)
    adjacency = "core" if rel >= 0.5 else ("adjacent" if item.origin == "adjacent" else "unrelated")
    return CandidateScores(
        story_ref=item.ref,
        relevance=round(rel, 4),
        because=because,
        adjacency=adjacency,
        novelty=round(max(0.0, min(1.0, novelty)), 4),
        importance=item.digest_importance if item.digest_importance is not None else DEFAULT_IMPORTANCE,
        importance_evidence=list(item.importance_evidence) or ["heuristic fallback"],
        dive_likelihood=round(0.8 * rel, 4),
        subtopics=list(item.subtopics),
        entities=list(item.entities),
    )


def _candidate_block(item: PoolItem, show_importance: bool) -> str:
    meta = {
        "story_ref": item.ref,
        "origin": item.origin,
        "beat": item.beat,
        "subtopics": item.subtopics,
        "entities": item.entities,
    }
    if show_importance and item.digest_importance is not None:
        meta["digest_importance"] = item.digest_importance
        meta["importance_evidence"] = item.importance_evidence[:5]
    body = f"{item.candidate.headline}\n{item.candidate.summary}".strip()
    return f"{meta}\n" + wrap(body, id=item.ref)


def finalize(
    raw: CandidateScores, item: PoolItem, known_ids: set[str], use_digest_importance: bool
) -> CandidateScores:
    importance = raw.importance
    evidence = list(raw.importance_evidence)
    if use_digest_importance and item.digest_importance is not None:
        importance = item.digest_importance
        evidence = list(item.importance_evidence) or evidence
    return raw.model_copy(
        update={
            "story_ref": item.ref,
            "because": [b for b in dict.fromkeys(raw.because) if b in known_ids],
            "importance": importance,
            "importance_evidence": evidence,
            "subtopics": raw.subtopics or list(item.subtopics),
            "entities": raw.entities or list(item.entities),
        }
    )


async def score_pool(
    cc: CallCtx,
    items: list[PoolItem],
    memory: MemorySnapshot,
    history: list[ShownStory],
    *,
    batch_size: int = 20,
    newsroom_separation: bool = True,
) -> dict[str, CandidateScores]:
    prompt = load_prompt("cos.score")
    known = {i.id for i in memory.interests}
    out: dict[str, CandidateScores] = {}
    budget_gone = False
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        got: dict[str, CandidateScores] = {}
        if not budget_gone:
            payload = (
                "## Memory\n"
                + str(memory_summary(memory, 40))
                + "\n\n## Recently shown headlines\n"
                + "\n".join(f"- {h.headline}" for h in history[-30:])
                + "\n\n## Candidates\n"
                + "\n\n".join(_candidate_block(it, newsroom_separation) for it in batch)
            )
            try:
                res = await call_structured(
                    cc, prompt, payload, ScoreBatch, step="C4", max_output_tokens=4096
                )
                got = {s.story_ref: s for s in res.scores}
            except BudgetExceeded:
                budget_gone = True
                cc.trace.degraded("C4", "token budget exceeded: heuristic scores for the rest")
            except StructuredOutputFailure:
                cc.trace.degraded("C4", "invalid score batch: heuristic scores for this batch", start=start)
        for it in batch:
            raw = got.get(it.ref)
            if raw is None:
                out[it.ref] = heuristic_score(it, memory, history)
                if got:
                    cc.trace.degraded("C4", "candidate missing from batch: heuristic score", story_ref=it.ref)
            else:
                out[it.ref] = finalize(raw, it, known, newsroom_separation)
    return out
