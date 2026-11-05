"""Build grader inputs from runner traces and compute every code metric for one run.

Story metadata (subtopics, entities, summary) comes from the day's digest entries, falling back to
the chief of staff's candidate scores for personal / adjacent finds. Mapping a personal candidate to
a hindsight-label story id needs W-C's clustering; until then `label_map` can be supplied.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from app.contracts.digest import DigestEntry
from eval.graders import brief as gbrief
from eval.graders import memory as gmem
from eval.graders import selection as gsel
from eval.graders import session as gsess
from eval.graders.records import QuestionTurn, ShownStory
from eval.runner.traces import RUNS_DIR, DayRecord, load_day_records, load_digest
from eval.sim.profile import SimProfile
from eval.sim.simulator import QUESTION_ACTIONS


def shown_stories(rec: DayRecord, digest_entries: Mapping[str, DigestEntry],
                  label_map: Mapping[str, str] | None = None) -> list[ShownStory]:
    outcomes = {o.story_ref: o for o in rec.session.stories}
    cand = {c.story_ref: c for c in rec.candidates}
    said: dict[str, list[str]] = {}
    for s in rec.brief.sentences:
        if s.story_ref:
            said.setdefault(s.story_ref, []).append(s.text)
    out = []
    for rank, st in enumerate(rec.brief.stories):
        de = digest_entries.get(st.story_ref)
        c = cand.get(st.story_ref)
        subs = de.subtopics if de else (c.scores.subtopics if c and c.scores else [])
        ents = de.entities if de else (c.scores.entities if c and c.scores else [])
        summary = de.summary if de else (c.summary if c else "")
        o = outcomes.get(st.story_ref)
        out.append(ShownStory(
            user_id=rec.profile_id, day=rec.day, day_index=rec.day_index, story_ref=st.story_ref,
            label_story_id=(label_map or {}).get(st.story_ref, st.story_ref if de else None),
            rank=rank, slot=st.slot, headline=st.headline,
            text=" ".join([st.headline, summary, *said.get(st.story_ref, [])]),
            subtopics=list(subs), entities=list(ents),
            heard=o.heard if o else "none", engaged=bool(o and o.engaged),
        ))
    return out


def question_turns(rec: DayRecord, answerability: Mapping[str, str] | None = None,
                   qa_labels: Mapping[str, str] | None = None, use_sim_intent: bool = False) -> list[QuestionTurn]:
    """Join simulator questions with the agent's per-turn tool log (aligned by utterance order).

    `answerability`: qid -> label from the T3 labelling. `use_sim_intent=True` falls back to the
    simulator's intended-unanswerable flag as a *proxy* label (report it as such)."""
    user_turns = [t for t in rec.session.turns if t.actor == "user"]
    agent_turns = rec.agent_turns
    out = []
    qi = 0
    for idx, t in enumerate(user_turns):
        if t.action not in QUESTION_ACTIONS:
            continue
        q = rec.session.questions[qi]
        qi += 1
        meta: dict[str, Any] = agent_turns[idx] if idx < len(agent_turns) else {}
        tools = list(meta.get("tools", []))
        label = (answerability or {}).get(q.qid)
        if label is None and use_sim_intent:
            label = "unanswerable" if q.intended_unanswerable else "unknown"
        jl = (qa_labels or {}).get(q.qid)
        outcome = "bookmarked" if (q.bookmarked or "bookmark_question" in tools) else \
            "abstained" if jl == "abstained" else "answered"
        out.append(QuestionTurn(qid=q.qid, user_id=rec.profile_id, day=rec.day, text=q.text,
                                answerability=label or "unknown", tools=tools, outcome=outcome, judge_label=jl,
                                ttft_ms=meta.get("ttft_ms")))
    return out


def grade_run(run_id: str, profiles: Mapping[str, SimProfile], *, runs_dir: Path = RUNS_DIR,
              digests_run_id: str | None = None, beat: str = "ai",
              important_first_seen: Mapping[str, date] | None = None,
              label_map: Mapping[str, str] | None = None, window: tuple[date, date] | None = None,
              use_sim_intent: bool = True) -> dict[str, Any]:
    """All code metrics for one run (no LLM calls). Importance recall needs hindsight labels."""
    recs = load_day_records(runs_dir, run_id)
    if not recs:
        raise FileNotFoundError(f"no traces for {run_id} in {runs_dir}")
    dg_run = digests_run_id or _manifest_digests(runs_dir, run_id)
    entries_by_day: dict[date, dict[str, DigestEntry]] = {}
    shown: list[ShownStory] = []
    turns: list[QuestionTurn] = []
    snaps: dict[str, dict[int, Any]] = {}
    lengths, heads = [], []
    for (_pid, d), rec in sorted(recs.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if d not in entries_by_day:
            dg = load_digest(runs_dir, dg_run, d, beat) if dg_run else None
            entries_by_day[d] = {e.story_id: e for e in dg.entries} if dg else {}
        ss = shown_stories(rec, entries_by_day[d], label_map)
        shown += ss
        turns += question_turns(rec, use_sim_intent=use_sim_intent)
        if rec.snapshot:
            snaps.setdefault(rec.profile_id, {})[rec.day_index] = rec.snapshot
        lengths.append((rec.brief.est_duration_s, profiles[rec.profile_id].delivery.length_s))
        heads += [(s.headline, s.entities) for s in ss]
    users = sorted({r.profile_id for r in recs.values()})
    start = min(d for _, d in recs)
    out: dict[str, Any] = {"run_id": run_id, "users": users, "user_days": len(recs)}
    if important_first_seen is not None:
        out["importance_recall"] = gsel.importance_recall(important_first_seen, shown, users, window=window).model_dump()
    out["relevance"] = gsel.mean_relevance(shown, profiles)
    out["topic_coverage"] = gsel.topic_coverage(shown, start).model_dump()
    out["latent_discovery"] = gsel.latent_discovery(shown, profiles).model_dump()
    out["interest_recovery"] = {u: [r.model_dump() for r in gmem.recovery_trajectory(s, profiles[u])]
                                for u, s in snaps.items()}
    out["drift_lag"] = {u: [x.model_dump() for x in gmem.drift_lags(s, profiles[u])] for u, s in snaps.items()}
    out["over_narrowing"] = gmem.over_narrowing_by_user(snaps, profiles)
    out["length_adherence"] = gbrief.length_adherence(lengths).model_dump()
    out["specificity"] = gbrief.specificity(heads).model_dump()
    out["prefetch_hit_rate"] = gsess.prefetch_hit_rate(turns).model_dump()
    out["abstention_proxy"] = gsess.abstention(turns).model_dump()  # sim-intent labels until T3 labelling
    out["replay_latency"] = gsess.replay_latency(turns).model_dump()
    return out


def _manifest_digests(runs_dir: Path, run_id: str) -> str | None:
    import json

    p = runs_dir / run_id / "manifest.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    return (m.get("reuse") or {}).get("digests_from") or f"{m['run_id']}-digests"
