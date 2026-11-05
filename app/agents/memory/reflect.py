"""`memory.reflect` (TDD §9.3 step 1): the agent decides what is worth remembering → `MemoryProposal`.

`update_memory_after_session` = reflect + the rules engine (`rules.apply_memory_update`). If the model call
fails, a deterministic proposal is derived from the session's signals so explicit statements are never lost.
"""

from __future__ import annotations

from datetime import datetime

from app.agents.cos.config import resolve_experiment
from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.prompts import load_prompt
from app.agents.cos.trace import RunTrace
from app.agents.cos.untrusted import sanitize
from app.agents.memory.config import MemoryConfig, memory_config
from app.agents.memory.rules import apply_memory_update, find_interest, opportunity
from app.config import loader
from app.config.loader import ExperimentCfg, ExperimentFlags
from app.contracts.agent_io import MemoryUpdateReport, SessionOutcome, SessionRecord
from app.contracts.common import BudgetExceeded, TokenBudget
from app.contracts.llm import ModelClient
from app.contracts.memory import MemoryProposal, MemorySnapshot, ProposalOp

SIGNAL_OP = {
    "more_like": ("reinforce", "explicit"),
    "keep_posted": ("add", "explicit"),
    "less_like": ("weaken", "explicit"),
    "stop_covering": ("remove", "explicit"),
    "bookmark": ("reinforce", "explicit"),
    "save_for_later": (
        "reinforce",
        "implicit",
    ),  # exposure counts it as engagement; weight via implicit strength
    "dive": ("reinforce", "implicit"),
    "followup": ("reinforce", "implicit"),
    "skip": ("weaken", "implicit"),
}


def _valid_subtopic(s: str) -> str:
    return s if s in loader.taxonomy().ids else "other"


def sanitize_proposal(p: MemoryProposal) -> MemoryProposal:
    """Map unknown subtopics to `other` and drop ops with empty labels."""
    ops = [
        op.model_copy(update={"subtopic": _valid_subtopic(op.subtopic)}) for op in p.ops if op.label.strip()
    ]
    return p.model_copy(update={"ops": ops})


def fallback_proposal(memory: MemorySnapshot, session: SessionRecord) -> MemoryProposal:
    """Deterministic proposal from signals: each signal targets the interests of its story, or its hint."""
    by_ref = {s.story_ref: s for s in session.shown}
    by_id = {i.id: i for i in memory.interests}
    ops: list[ProposalOp] = []
    seen: set[tuple[str, str, str]] = set()
    for sig in session.signals:
        if sig.type not in SIGNAL_OP:
            continue
        op, strength = SIGNAL_OP[sig.type]
        targets: list[tuple[str, str, str | None, str]] = []
        story = by_ref.get(sig.story_ref or "")
        if sig.interest_hint:
            known = find_interest(memory.interests, sig.interest_hint, None)
            sub = known.subtopic if known else (story.subtopics[0] if story and story.subtopics else "other")
            targets.append(
                (
                    sig.interest_hint,
                    sub,
                    known.entity if known else None,
                    "keep_posted" if sig.type == "keep_posted" else (known.kind if known else "topic"),
                )
            )
        elif story:
            for iid in story.interest_ids:
                if iid in by_id:
                    i = by_id[iid]
                    targets.append((i.label, i.subtopic, i.entity, i.kind))
        for label, sub, ent, kind in targets:
            key = (label.lower(), op, sig.type)
            if key in seen:
                continue
            seen.add(key)
            ops.append(
                ProposalOp(
                    op=op,
                    label=label,
                    subtopic=_valid_subtopic(sub),
                    entity=ent,
                    kind=kind,
                    strength=strength,
                    signal=sig.type,
                    evidence=[sig.turn_id] if sig.turn_id else [],
                    rationale="from signal (fallback)",
                )
            )
    return MemoryProposal(ops=ops)


def _payload(memory: MemorySnapshot, session: SessionRecord, o: float) -> str:
    interests = [
        {
            "id": i.id,
            "label": i.label,
            "subtopic": i.subtopic,
            "entity": i.entity,
            "kind": i.kind,
            "weight": round(i.weight, 3),
            "pinned": i.explicit_pin,
        }
        for i in memory.interests
    ]
    shown = [
        {
            "story_ref": s.story_ref,
            "headline": s.headline,
            "subtopics": s.subtopics,
            "heard": s.heard,
            "interest_ids": s.interest_ids,
        }
        for s in session.shown
    ]
    signals = [
        {
            "type": s.type,
            "story_ref": s.story_ref,
            "interest_hint": s.interest_hint,
            "explicit": s.explicit,
            "turn_id": s.turn_id,
        }
        for s in session.signals
    ]
    transcript = "\n".join(f"[{t.turn_id}] {t.role}: {sanitize(t.text)}" for t in session.transcript)
    return (
        f"## Current memory\n{interests}\n\n## Delivery preferences\n"
        f"{[p.model_dump() for p in memory.delivery]}\n\n## Session context\n{session.ctx.model_dump()} "
        f"(opportunity factor {o})\n\n## Stories in the brief\n{shown}\n\n## Signals\n{signals}\n\n"
        f"## Transcript (user turns are the user's own words; agent turns are ours)\n{transcript}\n\n"
        f"Valid subtopic ids: {sorted(loader.taxonomy().ids)}"
    )


async def reflect(
    cc: CallCtx, memory: MemorySnapshot, session: SessionRecord, cfg: MemoryConfig, flags: ExperimentFlags
) -> MemoryProposal:
    prompt = load_prompt("memory.reflect")
    o = opportunity(session.ctx, cfg, flags.session_ctx_weighting)
    try:
        p = await call_structured(
            cc, prompt, _payload(memory, session, o), MemoryProposal, step="memory.reflect"
        )
    except (BudgetExceeded, StructuredOutputFailure) as e:
        cc.trace.degraded("memory.reflect", f"{type(e).__name__}: proposal derived from signals")
        return fallback_proposal(memory, session)
    return sanitize_proposal(p)


async def update_memory_after_session(
    memory: MemorySnapshot,
    session: SessionRecord,
    *,
    model_client: ModelClient,
    flags: ExperimentCfg | ExperimentFlags | str | None = None,
    prior_sessions: list[SessionOutcome] = (),  # type: ignore[assignment]
    now: datetime,
    cfg: MemoryConfig | None = None,
    run_id: str = "live",
    job_id: str | None = None,
) -> tuple[MemorySnapshot, MemoryUpdateReport, MemoryProposal, RunTrace]:
    """Job `memory_update(session_id)`: reflect → rules. B2 static and B5 naive skip the model call."""
    _, fl, _ = resolve_experiment(flags)
    cfg = cfg or memory_config()
    trace = RunTrace(run_id=run_id, user_id=memory.user_id, job="memory_update")
    if fl.pipeline == "naive" or fl.memory == "static":
        proposal = MemoryProposal(ops=[])
    else:
        cc = CallCtx(
            client=model_client,
            budget=TokenBudget(input_tokens=cfg.budget_input_tokens),
            trace=trace,
            run_id=run_id,
            user_id=memory.user_id,
            job_id=job_id,
            cache_namespace=run_id,
        )
        proposal = await reflect(cc, memory, session, cfg, fl)
    new, report = apply_memory_update(
        memory, proposal, session, now=now, cfg=cfg, flags=fl, prior_sessions=list(prior_sessions)
    )
    trace.add(
        "memory.rules",
        "code",
        mode=report.mode,
        opportunity=report.opportunity,
        changes=[c.model_dump() for c in report.changes],
    )
    return new, report, proposal, trace
