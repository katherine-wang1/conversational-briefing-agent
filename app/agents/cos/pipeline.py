"""Chief-of-staff daily run (TDD §8): `run_cos_daily` → (BriefScript, source packs, trace).

Steps C1–C8 (C9 audio render and C10 persistence belong to the job handler). Experiment arms:
- `personalization=false` (B1): no C1–C4; digest stories by importance only (`select_importance_only`).
- `slots.outside_usual=0` (B3): via `selection_overrides` in the experiment config.
- `prefetch=false` (B4): light packs only; the caller must not hand packs to the voice agent.
- `pipeline=naive` (B5): `naive.run_naive`.
- `newsroom_separation=false` (A1): the scorer judges importance itself (digest importance hidden).
- `hn_signal=false` (A2): no HN reaction in packs.
- `claim_check=false`: skip C8.
"""

from __future__ import annotations

from app.agents.cos.claim_check import ToCheck, check_claims
from app.agents.cos.config import CosConfig, cos_config
from app.agents.cos.llm import CallCtx
from app.agents.cos.naive import run_naive
from app.agents.cos.packs import build_pack
from app.agents.cos.plan import plan_searches
from app.agents.cos.pool import PoolItem, build_pool
from app.agents.cos.score import score_pool
from app.agents.cos.search_loop import run_search_loop
from app.agents.cos.selection import ScoredItem, k_from_length, select, select_importance_only
from app.agents.cos.text import short_hash, tokens
from app.agents.cos.trace import RunTrace
from app.agents.cos.write import DEFAULT_OUTRO, assemble, est_duration_s, followup_sentences, write_brief
from app.config import loader
from app.config.loader import ExperimentCfg, ExperimentFlags
from app.contracts.agent_io import OpenQuestion, ShownStory, UserContext
from app.contracts.brief import BriefScript, BriefSentence, CandidateScores, SourcePack
from app.contracts.common import TokenBudget
from app.contracts.digest import Digest
from app.contracts.llm import ModelClient
from app.contracts.memory import MemorySnapshot
from app.contracts.tools import Tool


def subtopic_beats() -> dict[str, str | None]:
    return {s.id: (None if s.beat == "none" else s.beat) for s in loader.taxonomy().subtopics}


def to_scored(item: PoolItem, s: CandidateScores, beats: dict[str, str | None]) -> ScoredItem:
    beat = item.beat
    if beat is None:
        beat = next((beats[t] for t in s.subtopics if beats.get(t)), None)
    feats = (
        tokens(item.candidate.headline) | {e.lower() for e in s.entities} | {f"st:{t}" for t in s.subtopics}
    )
    return ScoredItem(
        story_ref=item.ref,
        importance=s.importance,
        relevance=s.relevance,
        novelty=s.novelty,
        adjacency=s.adjacency,
        beat=beat,
        origin=item.origin,
        features=frozenset(feats),
        update_of=item.update_of,
        dive_likelihood=s.dive_likelihood,
    )


def importance_only_scores(item: PoolItem, cfg: CosConfig) -> CandidateScores:
    mid = (cfg.packs.deep_threshold + cfg.packs.light_threshold) / 2  # standard depth for every story
    return CandidateScores(
        story_ref=item.ref,
        relevance=0.0,
        adjacency="core",
        novelty=1.0,
        importance=item.digest_importance or 0,
        importance_evidence=item.importance_evidence,
        dive_likelihood=mid,
        subtopics=item.subtopics,
        entities=item.entities,
    )


def brief_id(user_ctx: UserContext) -> str:
    return "b_" + short_hash(f"{user_ctx.user_id}|{user_ctx.local_date}|{user_ctx.run_id}|{user_ctx.arm}", 16)


def _renumber(sents: list[BriefSentence]) -> list[BriefSentence]:
    return [s.model_copy(update={"sid": f"s{n:03d}"}) for n, s in enumerate(sents)]


async def run_cos_daily(
    user_ctx: UserContext,
    digests: list[Digest],
    memory: MemorySnapshot,
    history: list[ShownStory],
    open_questions: list[OpenQuestion],
    flags: ExperimentCfg | ExperimentFlags | str | None,
    model_client: ModelClient,
    tools: dict[str, Tool],
    *,
    agent_cfg: dict | None = None,
) -> tuple[BriefScript, list[SourcePack], RunTrace]:
    cfg = cos_config(flags, agent_cfg)
    arm = user_ctx.arm if cfg.arm == "custom" else cfg.arm
    user_ctx = user_ctx.model_copy(update={"arm": arm})
    trace = RunTrace(run_id=user_ctx.run_id, user_id=user_ctx.user_id, job="cos_daily", arm=arm)
    budget = TokenBudget(input_tokens=cfg.budget.input_tokens, searches=cfg.budget.searches)
    cc = CallCtx(
        client=model_client,
        budget=budget,
        trace=trace,
        run_id=user_ctx.run_id,
        user_id=user_ctx.user_id,
        job_id=user_ctx.job_id,
        cache_namespace=user_ctx.run_id,
    )
    prefs = {p.key: p.value for p in memory.delivery}
    length_s = prefs.get("length_s")
    length_s = float(length_s) if isinstance(length_s, int | float) or str(length_s).isdigit() else None
    k = k_from_length(length_s, cfg.selection)

    if cfg.flags.pipeline == "naive":
        script, packs = await run_naive(
            cc, user_ctx, memory, tools, max_searches=cfg.budget.searches, k_target=k
        )
        script = script.model_copy(update={"brief_id": brief_id(user_ctx)})
        _finish_trace(trace, [budget], cfg.budget.input_tokens)
        return script, packs, trace

    # Two phases share the run's input-token budget: research (C1–C6) may use at most
    # (1 − write_reserve) of it, so writing and claim checking (C7–C8) always have tokens left.
    research = TokenBudget(
        input_tokens=int(cfg.budget.input_tokens * (1 - cfg.write_reserve)), searches=cfg.budget.searches
    )
    cc.budget = research

    beats = subtopic_beats()
    followed = list(user_ctx.followed_beats)
    if not cfg.flags.personalization:
        pool, notes = build_pool(
            digests,
            [],
            [h.model_copy(update={"saved_for_later": False}) for h in history],
            followed_beats=followed,
            update_requires_new_facts=cfg.selection.update_requires_new_facts,
            top_followed=cfg.digest_top_followed,
            top_other=cfg.digest_top_other,
            pool_max=cfg.pool_max,
        )
        scores = {it.ref: importance_only_scores(it, cfg) for it in pool}
        result = select_importance_only(
            [to_scored(it, scores[it.ref], beats) for it in pool], cfg.selection, k=k
        )
    else:
        plan = await plan_searches(cc, memory, open_questions, history, followed)
        trace.add("C1", "code", queries=[q.model_dump() for q in plan.queries])
        finds, sstate = await run_search_loop(
            cc,
            plan,
            tools,
            mode=user_ctx.mode,
            now=user_ctx.now,
            max_searches=cfg.budget.searches,
            lookback_days=cfg.search_lookback_days,
            max_rounds=cfg.max_refine_rounds,
        )
        trace.add("C2", "code", searches_used=sstate.used, finds=len(finds))
        pool, notes = build_pool(
            digests,
            finds,
            history,
            followed_beats=followed,
            update_requires_new_facts=cfg.selection.update_requires_new_facts,
            top_followed=cfg.digest_top_followed,
            top_other=cfg.digest_top_other,
            pool_max=cfg.pool_max,
        )
        scores = await score_pool(
            cc,
            pool,
            memory,
            history,
            batch_size=cfg.score_batch_size,
            newsroom_separation=cfg.flags.newsroom_separation,
        )
        result = select(
            [to_scored(it, scores[it.ref], beats) for it in pool], cfg.selection, k=k, followed_beats=followed
        )
    trace.add(
        "C3",
        "code",
        pool_size=len(pool),
        notes=notes[:50],
        origins={o: sum(1 for p in pool if p.origin == o) for o in sorted({p.origin for p in pool})},
    )
    trace.add(
        "C5",
        "code",
        k=k,
        slot_targets=result.slot_targets,
        notes=result.notes,
        selected=[s.model_dump() for s in result.selected],
        scores={ref: s.model_dump() for ref, s in scores.items()},
    )

    by_ref = {it.ref: it for it in pool}
    packs: list[SourcePack] = []
    for s in result.selected:
        packs.append(
            await build_pack(
                cc,
                by_ref[s.story_ref],
                scores.get(s.story_ref),
                tools,
                mode=user_ctx.mode,
                now=user_ctx.now,
                cfg=cfg,
                history=history,
            )
        )
    pack_by_ref = {p.story_ref: p for p in packs}

    answered = [
        q for q in open_questions if q.status == "answered" and q.answer and q.attached_to_brief is None
    ]
    for q in answered:
        fp = SourcePack(
            story_ref=f"followup:{q.id}",
            headline=q.question,
            facts=[],
            passages=list(q.answer.passages),
            depth="light",
        )
        packs.append(fp)
        pack_by_ref[fp.story_ref] = fp
    headlines = {it.ref: it.candidate.headline for it in pool}
    entities = {ref: list(s.entities) for ref, s in scores.items()}

    writing = TokenBudget(input_tokens=cfg.budget.input_tokens - research.used_input_tokens)
    cc.budget = writing
    draft = await write_brief(
        cc,
        result.selected,
        headlines,
        pack_by_ref,
        memory.delivery,
        answered,
        int(length_s) if length_s else None,
    )
    sentences, stories, wnotes = assemble(
        draft, result.selected, headlines, pack_by_ref, followup_sentences(answered), entities
    )
    trace.add("C7", "code", notes=wnotes, n_sentences=len(sentences))

    if cfg.flags.claim_check:
        passages = {p.passage_id: p for pk in packs for p in pk.passages}
        to_check = [
            ToCheck(s.sid, s.text, s.cites)
            for s in sentences
            if s.kind == "story" or (s.cites and s.kind != "followup_answer")
        ]
        outcomes = {o.sid: o for o in await check_claims(cc, to_check, passages)}
        kept = []
        for s in sentences:
            o = outcomes.get(s.sid)
            if o is None:
                kept.append(s)
            elif o.text is not None:
                kept.append(s.model_copy(update={"text": o.text}))
        trace.add("C8", "code", checks=[o.record.model_dump() for o in outcomes.values()])
        sentences = kept
        live = {s.story_ref for s in sentences if s.kind == "story"}
        stories = [st for st in stories if st.story_ref in live]
        sentences = [
            s for s in sentences if s.kind != "transition" or s.story_ref is None or s.story_ref in live
        ]
        if not sentences or sentences[-1].kind != "outro":
            sentences.append(BriefSentence(sid="tmp", text=DEFAULT_OUTRO, kind="outro"))

    sentences = _renumber(sentences)
    script = BriefScript(
        brief_id=brief_id(user_ctx),
        user_id=user_ctx.user_id,
        local_date=user_ctx.local_date,
        arm=arm,
        stories=stories,
        sentences=sentences,
        est_duration_s=est_duration_s(sentences),
    )
    _finish_trace(trace, [research, writing], cfg.budget.input_tokens)
    return script, packs, trace


def _finish_trace(trace: RunTrace, budgets: list[TokenBudget], total_input: int) -> None:
    trace.add(
        "end",
        "code",
        used_input_tokens=sum(b.used_input_tokens for b in budgets),
        used_output_tokens=sum(b.used_output_tokens for b in budgets),
        used_searches=sum(b.used_searches for b in budgets),
        budget_input_tokens=total_input,
    )
