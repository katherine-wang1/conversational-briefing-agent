"""Newsroom run: N2 normalise -> N3 cluster -> N4 features -> N5 score -> N6 digest (TDD §7).

Entry point: `run_newsroom(beat_id, at, observations, model_client, tools, flags)`.
It takes **no user context at all** (NEWS-2): no user id, no memory, and the only tool it exposes
to the model is `fetch`. N1 (collection) is the caller's job: pass the beat's
`HeadlineObservation`s up to `at` (more is fine; they are filtered to the run window).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agents.newsroom.clustering import Embedder, FastEmbedEmbedder
from app.agents.newsroom.features import SourceIndex, compute_features, importance_rules
from app.agents.newsroom.llm_steps import LlmTiebreaker, RunCtx, Scorer
from app.agents.newsroom.normalize import normalize
from app.agents.newsroom.prompts import load_prompt
from app.agents.newsroom.settings import Settings
from app.agents.newsroom.stories import build_stories
from app.agents.newsroom.trace import NewsroomTrace, Trace, TraceStep
from app.config import loader
from app.config.loader import ExperimentFlags
from app.contracts.common import Mode, TokenBudget
from app.contracts.digest import Digest, DigestEntry, DigestEntryLLM, Story
from app.contracts.llm import ModelClient
from app.contracts.sources import HeadlineObservation
from app.contracts.tools import Tool

SCORE_PROMPT = "newsroom.score"
TIEBREAK_PROMPT = "newsroom.cluster_tiebreak"
# Prompt fragments that differ under ablation A2 (hn_signal=false): no HN wording at all.
FEATURE_LIST = "coverage across independent outlets, front-page hits, best rank, source tier, velocity"
FEATURE_LIST_HN = (
    "coverage across independent outlets, front-page hits, best rank, source tier, Hacker News points "
    "and comments as observed at that time, velocity"
)
EVIDENCE_EXAMPLES = (
    '"techmeme rank 2", "7 independent domains", "frontpage_hits 4", "primary source present", '
    '"single source (arxiv_cs_cl)"'
)
EVIDENCE_EXAMPLES_HN = EVIDENCE_EXAMPLES[:-1] + ', "hn_front 412 points at observation"'


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _fetch_tool(tools: Mapping[str, Tool] | Sequence[Tool] | None) -> Tool | None:
    """Only `fetch` is ever exposed to the newsroom model; other tools are ignored (NEWS-2)."""
    if not tools:
        return None
    if isinstance(tools, Mapping):
        return tools.get("fetch")
    return next((t for t in tools if getattr(t, "name", None) == "fetch"), None)


def default_embedder(settings: Settings) -> Embedder:
    return FastEmbedEmbedder(settings.newsroom.embedder.model)


def digest_version(
    settings: Settings, prompt_hashes: Mapping[str, str], flags: ExperimentFlags, embedder_name: str
) -> str:
    blob = json.dumps(
        {
            "settings": settings.model_dump(),
            "prompts": dict(prompt_hashes),
            "hn_signal": flags.hn_signal,
            "embedder": embedder_name,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def run_newsroom(
    beat_id: str,
    at: datetime,
    observations: Sequence[HeadlineObservation],
    model_client: ModelClient,
    tools: Mapping[str, Tool] | Sequence[Tool] | None,
    flags: ExperimentFlags | Mapping[str, Any] | None = None,
    *,
    history: Sequence[Story] = (),
    previous_run_at: datetime | None = None,
    run_id: str = "live",
    mode: Mode = "live",
    job_id: str | None = None,
    cache_namespace: str | None = None,
    embedder: Embedder | None = None,
    settings: Settings | None = None,
    budget: TokenBudget | None = None,
) -> tuple[Digest, Trace]:
    """Produce one beat's digest at `at`.

    Optional keyword arguments (all with safe defaults):
    - `history`: `Story` objects from earlier runs (e.g. the previous trace's `stories`), used for
      story identity and `update_of`. Only the last `identity_lookback_days` are considered.
    - `previous_run_at`: for `velocity` (new items since the last run); default `at - window_hours`.
    - `run_id`, `mode`, `job_id`, `cache_namespace`: provenance passed to model and tool calls.
    - `embedder`: defaults to fastembed with the configured model.
    - `settings`, `budget`: default from config/agent.yaml (`newsroom`, `clustering`,
      `budgets.newsroom_run`).
    """
    at = _utc(at)
    fl = flags if isinstance(flags, ExperimentFlags) else ExperimentFlags(**dict(flags or {}))
    st = settings or Settings.from_config()
    beat = loader.beats()[beat_id]
    emb = embedder or default_embedder(st)
    budget = budget or TokenBudget(input_tokens=st.budget.input_tokens, fetches=st.budget.fetches)
    score_prompt, tb_prompt = load_prompt(SCORE_PROMPT), load_prompt(TIEBREAK_PROMPT)
    prompt_hashes = {SCORE_PROMPT: score_prompt.sha256, TIEBREAK_PROMPT: tb_prompt.sha256}
    version = digest_version(st, prompt_hashes, fl, emb.name)
    trace = NewsroomTrace(
        run_id=run_id,
        beat=beat_id,
        at=at,
        version=version,
        flags=fl.model_dump(),
        settings=st.model_dump(),
        prompt_hashes=prompt_hashes,
        embedder=emb.name,
    )
    ctx = RunCtx(
        run_id=run_id,
        beat=beat_id,
        at=at,
        mode=mode,
        budget=budget,
        trace=trace,
        cache_namespace=cache_namespace or run_id,
        job_id=job_id,
    )
    sources = SourceIndex(beat, st.newsroom.primary_sources.get(beat_id, []))

    # ---- N2 normalise ----------------------------------------------------------------------------
    window_start = at - timedelta(hours=st.newsroom.window_hours)
    kept: list[HeadlineObservation] = []
    dropped = {"other_beat": 0, "out_of_window": 0, "future": 0, "hn_ablation": 0}
    for o in observations:
        if o.beat != beat_id:
            dropped["other_beat"] += 1
        elif _utc(o.observed_at) > at:
            dropped["future"] += 1  # never use anything observed after the run time
        elif _utc(o.observed_at) <= window_start:
            dropped["out_of_window"] += 1
        elif not fl.hn_signal and sources.is_hn(o.source_id):
            dropped["hn_ablation"] += 1
        else:
            kept.append(o.model_copy(update={"observed_at": _utc(o.observed_at)}))
    items = normalize(kept, {s.id: s.tier for s in beat.headline_sources})
    trace.steps.append(
        TraceStep(
            step="N2",
            name="normalise",
            detail={
                "observations_in": len(observations),
                "observations_kept": len(kept),
                "dropped": dropped,
                "items": len(items),
                "unresolved_aggregator_links": sum(it.unresolved_aggregator for it in items),
            },
        )
    )

    # ---- N3 cluster + identity -------------------------------------------------------------------
    tiebreaker = LlmTiebreaker(
        model_client,
        tb_prompt,
        ctx,
        batch_size=st.newsroom.tiebreak_batch_size,
        max_output_tokens=st.newsroom.tiebreak_max_output_tokens,
    )
    sr = await build_stories(
        items,
        beat=beat_id,
        at=at,
        sources=sources,
        embedder=emb,
        tau=st.clustering.tau,
        band=st.clustering.tiebreak_band,
        lookback_days=st.clustering.identity_lookback_days,
        update_gap_hours=st.newsroom.update_gap_hours,
        history=history,
        tiebreaker=tiebreaker,
        max_tiebreak_pairs=st.newsroom.tiebreak_max_pairs,
    )
    drafts = sr.drafts
    trace.steps.append(
        TraceStep(
            step="N3",
            name="cluster",
            detail={
                "stories": len(drafts),
                "multi_item_stories": sum(len(d.items) > 1 for d in drafts),
                "tiebreak_pairs": len(sr.clustering.tiebreaks),
                "tiebreak_merged": sum(1 for _, same in sr.clustering.tiebreaks if same),
                "prior_matches": [
                    {
                        "story_id": d.story_id,
                        "matched": d.match.ref_id,
                        "method": d.match.method,
                        "similarity": d.match.similarity,
                    }
                    for d in drafts
                    if d.match is not None
                ],
                "continued": sum(d.continued_from is not None for d in drafts),
                "updates": sum(d.update_of is not None and d.continued_from is None for d in drafts),
            },
        )
    )

    # ---- N4 features -----------------------------------------------------------------------------
    last_run = _utc(previous_run_at) if previous_run_at else window_start
    features = {
        d.story_id: compute_features(d.items, sources, at=at, last_run_at=last_run, hn_signal=fl.hn_signal)
        for d in drafts
    }
    rules = {
        sid: importance_rules(f, st.newsroom.rules_weights, hn_signal=fl.hn_signal)
        for sid, f in features.items()
    }
    stories = [
        Story(
            story_id=d.story_id,
            beat=beat_id,
            title=d.title,
            first_seen=d.first_seen,
            item_urls=d.item_urls,
            features=features[d.story_id],
            update_of=d.update_of,
        )
        for d in drafts
    ]
    trace.stories = sorted(stories, key=lambda s: s.story_id)
    by_rules = sorted(drafts, key=lambda d: (-rules[d.story_id], d.story_id))
    candidates = by_rules[: st.newsroom.score_candidates]
    trace.steps.append(
        TraceStep(
            step="N4",
            name="features",
            detail={
                "candidates_for_scoring": [d.story_id for d in candidates],
                "importance_rules": {d.story_id: rules[d.story_id] for d in candidates},
            },
        )
    )

    # ---- N5 score --------------------------------------------------------------------------------
    subtopic_lines = (
        "\n".join(f"- {s.id}: {s.description}" for s in loader.taxonomy().subtopics if s.id in beat.subtopics)
        + "\n- other: none of the above"
    )
    system_text = score_prompt.render(
        beat_name=beat.name,
        beat_scope=beat.scope.strip(),
        beat_boundaries="\n".join(f"- {b}" for b in beat.boundaries) or "- (none)",
        subtopics=subtopic_lines,
        fetch_budget=budget.fetches if budget.fetches is not None else 0,
        feature_list=FEATURE_LIST_HN if fl.hn_signal else FEATURE_LIST,
        evidence_examples=EVIDENCE_EXAMPLES_HN if fl.hn_signal else EVIDENCE_EXAMPLES,
    )
    scorer = Scorer(
        model_client,
        score_prompt,
        ctx,
        system_text=system_text,
        sources=sources,
        beat_subtopics=beat.subtopics,
        hn_signal=fl.hn_signal,
        fetch_tool=_fetch_tool(tools),
        fetch_max_tokens=st.newsroom.fetch_max_tokens,
        max_output_tokens=st.newsroom.score_max_output_tokens,
    )
    size = max(1, st.newsroom.score_batch_size)
    batches = [candidates[i : i + size] for i in range(0, len(candidates), size)]
    scored: list[DigestEntryLLM] = []
    for n, batch in enumerate(batches, 1):
        scored.extend(await scorer.score_batch(batch, features, rules, batch_no=n, n_batches=len(batches)))
    trace.steps.append(
        TraceStep(
            step="N5",
            name="score",
            detail={
                "batches": len(batches),
                "scored": len(scored),
                "rules_only": sum(1 for e in scored if (e.notes_for_editor or "").startswith("rules-only")),
                "fetches_used": budget.used_fetches,
                "input_tokens_used": budget.used_input_tokens,
            },
        )
    )

    # ---- N6 digest -------------------------------------------------------------------------------
    draft_by_id = {d.story_id: d for d in drafts}

    def entry_urls(sid: str) -> list[str]:
        its = sorted(
            draft_by_id[sid].items,
            key=lambda it: (min((sources.tier(s) or 9) for s in it.source_ids), it.first_seen, it.url),
        )
        return [it.url for it in its]

    above = [e for e in scored if e.importance >= st.newsroom.score_floor]
    ranked = sorted(above, key=lambda e: (-e.importance, -rules[e.story_id], e.story_id))[: beat.digest_size]
    entries = [
        DigestEntry(
            **e.model_dump(),
            importance_rules=rules[e.story_id],
            features=features[e.story_id],
            urls=entry_urls(e.story_id),
        )
        for e in ranked
    ]
    digest_id = (
        f"{beat_id}-{at:%Y%m%dT%H%MZ}-"
        + hashlib.sha256(f"{run_id}|{beat_id}|{at.isoformat()}|{version}".encode()).hexdigest()[:8]
    )
    digest = Digest(digest_id=digest_id, run_id=run_id, beat=beat_id, at=at, version=version, entries=entries)
    trace.steps.append(
        TraceStep(
            step="N6",
            name="digest",
            detail={
                "digest_id": digest_id,
                "entries": len(entries),
                "below_floor": len(scored) - len(above),
                "floor": st.newsroom.score_floor,
                "digest_size": beat.digest_size,
            },
        )
    )
    return digest, trace
