"""Memory rules engine (TDD §9.3). Pure and deterministic: no model calls, no clock, no I/O.

`apply_memory_update(memory, proposal, session, ...)` applies a `MemoryProposal` (from `memory.reflect`)
to a `MemorySnapshot` in this order:

1. **Decay** every unpinned interest to `today`: `w ← w · 0.5^(Δdays / h)`, with `h = half_life_explicit_d`
   for `explicit` / `onboarding` interests and `half_life_implicit_d` for `implicit` / `exploration` ones.
   Δdays is counted from `memory.as_of`.
2. **Opportunity** `o`: 1.0 if the session ended `natural` and `time_available_s ≥ brief_length_s` (or either
   is unknown); otherwise `cut_short_opportunity` (0.3). `idle`, `error`, `cut_short` and an unknown end
   reason count as not-full opportunity (MEM-3: only infer disinterest when the user had time).
   A3 (`session_ctx_weighting=false`): `o = 1` always.
3. **Exposure** from the session's stories: `shown_n += 1` for each interest of each story actually heard
   (`heard` and no `not_reached` signal); `engaged_n += 1` for each interest of each story with a
   `dive` / `followup` / `save_for_later` signal (engaged stories always count as heard). Once per story.
4. **Ops**, in proposal order. Interests are matched by entity (if both have one), else by label
   (case-insensitive).
   - explicit `add` / `reinforce`: `w += η_e` (creates the interest if missing, source `explicit`)
   - explicit `weaken`: `w -= η_e` (floored at 0)
   - explicit `pin` / `unpin` / `remove`: set / clear `explicit_pin`, delete the interest
   - implicit `add` / `reinforce`: `w += η_i · s(signal)` with `s` from `implicit_strength`
     (dive 0.5, followup 0.3, save_for_later 0.4; other positive signals use the dive strength)
   - implicit `weaken`: `w -= η_i · s(skip) · o`, **only if** the interest was skipped in ≥ `min_sessions`
     of the last `window` sessions with `o = 1.0` (current session included when its `o = 1.0`).
     A3 removes this condition.
   - implicit `pin` / `unpin` / `remove`, and any op whose signal is `not_reached`, are ignored.
5. **Blend** for implicit-sourced interests (`implicit`, `exploration`) whose exposure changed this
   session: `w ← (1 − β)·w + β·e`, `e = (engaged_n + a) / (shown_n + a + b)` (Beta prior, β = 0.3).
6. **Concentration guard**: no subtopic may hold more than `max_subtopic_share` of total weight. Over-share
   groups are scaled down exactly (water-filling); groups containing a pinned interest are exempt. Interests
   in subtopic `other` are grouped by entity/label rather than together. If the cap cannot be met (every
   remaining group would be capped), the guard is skipped and noted.
7. **Floor**: drop unpinned interests with `w < floor` whose `last_reinforced_at` is ≥ `floor_after_days`
   old (or unknown).
8. **Delivery prefs**: explicit changes set the value (confidence ≥ 0.8); implicit changes only fill a
   missing pref or replace one with confidence < 0.5 (confidence 0.4).

Modes: B2 (`memory: static`) returns the memory unchanged (signals are still logged by the caller, and the
session outcome is still returned). B5 (`pipeline: naive`) appends the user's explicit statements to
`profile_text` and touches nothing else.

Confidence updates are not fixed by the TDD; this engine uses: explicit ops raise confidence to ≥ 0.8,
implicit reinforcement adds 0.05 (capped at 0.8), new implicit interests start at 0.2.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from app.agents.cos.text import short_hash
from app.agents.memory.config import MemoryConfig
from app.config.loader import ExperimentFlags
from app.contracts.agent_io import MemoryChange, MemoryUpdateReport, SessionOutcome, SessionRecord
from app.contracts.memory import (
    DeliveryChange,
    DeliveryPref,
    Interest,
    MemoryProposal,
    MemorySnapshot,
    ProposalOp,
    SessionContext,
)

ENGAGED_SIGNALS = frozenset({"dive", "followup", "save_for_later"})
POSITIVE_SIGNALS = frozenset({"dive", "followup", "save_for_later", "bookmark", "more_like", "keep_posted"})
EXPLICIT_HALF_LIFE_SOURCES = frozenset({"explicit", "onboarding"})
IMPLICIT_SOURCES = frozenset({"implicit", "exploration"})
EPS = 1e-12


# ---- small pure helpers ------------------------------------------------------------------------


def interest_key(label: str, subtopic: str, entity: str | None) -> str:
    return f"{subtopic}|{(entity or label).strip().lower()}"


def interest_id(label: str, subtopic: str, entity: str | None) -> str:
    return "i_" + short_hash(interest_key(label, subtopic, entity), 10)


def find_interest(interests: list[Interest], label: str, entity: str | None) -> Interest | None:
    if entity:
        e = entity.strip().lower()
        for i in interests:
            if i.entity and i.entity.strip().lower() == e:
                return i
    lab = label.strip().lower()
    for i in interests:
        if i.label.strip().lower() == lab:
            return i
    return None


def opportunity(ctx: SessionContext, cfg: MemoryConfig, session_ctx_weighting: bool = True) -> float:
    if not session_ctx_weighting:
        return 1.0
    short_time = (
        ctx.time_available_s is not None
        and ctx.brief_length_s is not None
        and ctx.time_available_s < ctx.brief_length_s
    )
    if ctx.end_reason == "natural" and not short_time:
        return 1.0
    return cfg.cut_short_opportunity


def implicit_estimate(i: Interest, cfg: MemoryConfig) -> float:
    a, b = cfg.beta_prior.a, cfg.beta_prior.b
    return (i.engaged_n + a) / (i.shown_n + a + b)


def half_life(i: Interest, cfg: MemoryConfig) -> float:
    return cfg.half_life_explicit_d if i.source in EXPLICIT_HALF_LIFE_SOURCES else cfg.half_life_implicit_d


def decay(
    memory: MemorySnapshot, today: date, cfg: MemoryConfig
) -> tuple[MemorySnapshot, list[MemoryChange]]:
    """Daily decay from `memory.as_of` to `today`. Pinned interests do not decay."""
    days = (today - memory.as_of).days
    if days <= 0:
        return memory.model_copy(deep=True), []
    out, changes = [], []
    for i in memory.interests:
        if i.explicit_pin:
            out.append(i.model_copy())
            continue
        w = i.weight * 0.5 ** (days / half_life(i, cfg))
        changes.append(
            MemoryChange(
                interest_id=i.id,
                label=i.label,
                action="decay",
                delta=w - i.weight,
                reason=f"{days}d, half-life {half_life(i, cfg):g}d",
            )
        )
        out.append(i.model_copy(update={"weight": w}))
    return memory.model_copy(update={"interests": out, "as_of": today}, deep=True), changes


def skip_rule_met(
    interest_id_: str,
    current: SessionOutcome,
    prior: Iterable[SessionOutcome],
    cfg: MemoryConfig,
    session_ctx_weighting: bool = True,
) -> bool:
    if not session_ctx_weighting:
        return True
    sessions = sorted([*prior, current], key=lambda s: (s.at, s.session_id))
    seen: set[str] = set()
    full = []
    for s in sessions:
        if s.session_id in seen:
            continue
        seen.add(s.session_id)
        if s.opportunity >= 1.0 - EPS:
            full.append(s)
    window = full[-cfg.skip_rule.window :] if cfg.skip_rule.window > 0 else []
    return sum(1 for s in window if interest_id_ in s.skipped_interest_ids) >= cfg.skip_rule.min_sessions


def concentration_guard(
    interests: list[Interest], cfg: MemoryConfig
) -> tuple[list[Interest], list[MemoryChange]]:
    """Cap any subtopic group at `max_subtopic_share` of total weight (exact water-filling)."""
    c = cfg.max_subtopic_share
    if c <= 0 or c >= 1 or not interests:
        return interests, []

    def gkey(i: Interest) -> str:
        return i.subtopic if i.subtopic != "other" else f"other:{(i.entity or i.label).strip().lower()}"

    groups: dict[str, list[Interest]] = {}
    for i in interests:
        groups.setdefault(gkey(i), []).append(i)
    weight = {g: sum(i.weight for i in members) for g, members in groups.items()}
    exempt = {g for g, members in groups.items() if any(i.explicit_pin for i in members)}
    total = sum(weight.values())
    if total <= EPS:
        return interests, []

    capped: set[str] = set()
    while True:
        uncapped = sum(w for g, w in weight.items() if g not in capped)
        if uncapped <= EPS or len(capped) * c >= 1:
            return interests, [
                MemoryChange(label="*", action="cap_skipped", reason="cap infeasible: too few subtopics")
            ]
        t_new = uncapped / (1 - len(capped) * c)
        over = {g for g, w in weight.items() if g not in capped and g not in exempt and w > c * t_new + 1e-9}
        if not over:
            break
        capped |= over
    if not capped:
        return interests, []
    factors = {g: c * t_new / weight[g] for g in capped}
    out, changes = [], []
    for i in interests:
        g = gkey(i)
        if g in factors:
            w = i.weight * factors[g]
            changes.append(
                MemoryChange(
                    interest_id=i.id,
                    label=i.label,
                    action="cap",
                    delta=w - i.weight,
                    reason=f"subtopic group {g} capped at {c:.0%}",
                )
            )
            out.append(i.model_copy(update={"weight": w}))
        else:
            out.append(i)
    return out, changes


def apply_floor(
    interests: list[Interest], now: datetime, cfg: MemoryConfig
) -> tuple[list[Interest], list[MemoryChange]]:
    out, changes = [], []
    for i in interests:
        old_enough = i.last_reinforced_at is None or (now - i.last_reinforced_at).days >= cfg.floor_after_days
        if not i.explicit_pin and i.weight < cfg.floor and old_enough:
            changes.append(
                MemoryChange(
                    interest_id=i.id,
                    label=i.label,
                    action="floor_drop",
                    delta=-i.weight,
                    reason=f"w < {cfg.floor} for ≥ {cfg.floor_after_days}d",
                )
            )
            continue
        out.append(i)
    return out, changes


def apply_delivery(
    prefs: list[DeliveryPref], changes_in: list[DeliveryChange], cfg: MemoryConfig
) -> tuple[list[DeliveryPref], list[MemoryChange]]:
    by_key = {p.key: p for p in prefs}
    changes = []
    for ch in changes_in:
        cur = by_key.get(ch.key)
        if ch.explicit:
            conf = max(cur.confidence if cur else 0.0, cfg.explicit_confidence)
            by_key[ch.key] = DeliveryPref(key=ch.key, value=ch.value, confidence=conf)
            changes.append(MemoryChange(label=f"delivery.{ch.key}", action="delivery_set", reason="explicit"))
        elif cur is None or cur.confidence < 0.5:
            by_key[ch.key] = DeliveryPref(
                key=ch.key, value=ch.value, confidence=cfg.implicit_delivery_confidence
            )
            changes.append(MemoryChange(label=f"delivery.{ch.key}", action="delivery_set", reason="implicit"))
        else:
            changes.append(
                MemoryChange(
                    label=f"delivery.{ch.key}",
                    action="delivery_kept",
                    reason=f"implicit change ignored: existing confidence {cur.confidence}",
                )
            )
    order = [p.key for p in prefs] + sorted(k for k in by_key if k not in {p.key for p in prefs})
    return [by_key[k] for k in order], changes


# ---- session → exposure / skips ------------------------------------------------------------------


def session_exposure(
    session: SessionRecord, known_ids: set[str]
) -> tuple[dict[str, int], dict[str, int], set[str]]:
    """Return (shown increments, engaged increments, skipped interest ids) for one session."""
    by_story: dict[str, set[str]] = {}
    for s in session.signals:
        if s.story_ref:
            by_story.setdefault(s.story_ref, set()).add(s.type)
    shown: dict[str, int] = {}
    engaged: dict[str, int] = {}
    skipped: set[str] = set()
    for story in session.shown:
        types = by_story.get(story.story_ref, set())
        ids = [i for i in dict.fromkeys(story.interest_ids) if i in known_ids]
        is_engaged = bool(types & ENGAGED_SIGNALS) or story.engaged
        heard = (story.heard and "not_reached" not in types) or is_engaged
        if heard:
            for i in ids:
                shown[i] = shown.get(i, 0) + 1
        if is_engaged:
            for i in ids:
                engaged[i] = engaged.get(i, 0) + 1
        if "skip" in types and heard:
            skipped.update(ids)
    return shown, engaged, skipped


# ---- main entry point ----------------------------------------------------------------------------


def apply_memory_update(
    memory: MemorySnapshot,
    proposal: MemoryProposal,
    session: SessionRecord,
    *,
    now: datetime,
    cfg: MemoryConfig,
    flags: ExperimentFlags | None = None,
    prior_sessions: Iterable[SessionOutcome] = (),
    today: date | None = None,
) -> tuple[MemorySnapshot, MemoryUpdateReport]:
    """Apply one session's proposal to memory. Pure: same inputs → same outputs."""
    flags = flags or ExperimentFlags()
    today = today or now.date()
    prior = list(prior_sessions)
    weighting = flags.session_ctx_weighting
    o = opportunity(session.ctx, cfg, weighting)

    if flags.pipeline == "naive":
        return _naive_update(memory, session, today, o)

    known = {i.id for i in memory.interests}
    shown_inc, engaged_inc, skipped_ids = session_exposure(session, known)
    # implicit skip-weaken ops also mark the interest as skipped this session
    for op in proposal.ops:
        if op.strength == "implicit" and op.op == "weaken" and op.signal == "skip":
            i = find_interest(memory.interests, op.label, op.entity)
            if i:
                skipped_ids.add(i.id)
    outcome = SessionOutcome(
        session_id=session.session_id,
        at=session.started_at,
        opportunity=o,
        skipped_interest_ids=sorted(skipped_ids),
    )
    report = MemoryUpdateReport(session_id=session.session_id, opportunity=o, outcome=outcome)

    if flags.memory == "static":
        report.mode = "static"
        report.changes.append(MemoryChange(label="*", action="frozen", reason="B2 static profile"))
        return memory.model_copy(deep=True), report

    mem, changes = decay(memory, today, cfg)
    report.changes += changes
    interests = list(mem.interests)

    # exposure
    touched: set[str] = set()
    for idx, i in enumerate(interests):
        ds, de = shown_inc.get(i.id, 0), engaged_inc.get(i.id, 0)
        if ds or de:
            interests[idx] = i.model_copy(update={"shown_n": i.shown_n + ds, "engaged_n": i.engaged_n + de})
            touched.add(i.id)
            report.changes.append(
                MemoryChange(
                    interest_id=i.id, label=i.label, action="exposure", reason=f"shown +{ds}, engaged +{de}"
                )
            )

    # ops
    for op in proposal.ops:
        interests, ch = _apply_op(interests, op, session, now, o, outcome, prior, cfg, weighting)
        report.changes += ch

    # blend
    for idx, i in enumerate(interests):
        if i.id in touched and i.source in IMPLICIT_SOURCES:
            e = implicit_estimate(i, cfg)
            w = (1 - cfg.blend_implicit) * i.weight + cfg.blend_implicit * e
            interests[idx] = i.model_copy(update={"weight": w})
            report.changes.append(
                MemoryChange(
                    interest_id=i.id, label=i.label, action="blend", delta=w - i.weight, reason=f"e={e:.3f}"
                )
            )

    interests, ch = concentration_guard(interests, cfg)
    report.changes += ch
    interests, ch = apply_floor(interests, now, cfg)
    report.changes += ch
    delivery, ch = apply_delivery(mem.delivery, proposal.delivery, cfg)
    report.changes += ch

    return mem.model_copy(update={"interests": interests, "delivery": delivery, "as_of": today}), report


def _evidence(i: Interest, op: ProposalOp, session_id: str, cfg: MemoryConfig) -> list[str]:
    ev = list(i.evidence)
    for e in [session_id, *op.evidence]:
        if e and e not in ev:
            ev.append(e)
    return ev[-cfg.evidence_max :]


def _apply_op(
    interests: list[Interest],
    op: ProposalOp,
    session: SessionRecord,
    now: datetime,
    o: float,
    outcome: SessionOutcome,
    prior: list[SessionOutcome],
    cfg: MemoryConfig,
    weighting: bool,
) -> tuple[list[Interest], list[MemoryChange]]:
    def note(action: str, reason: str, i: Interest | None = None, delta: float = 0.0) -> list[MemoryChange]:
        return [
            MemoryChange(
                interest_id=i.id if i else None, label=op.label, action=action, delta=delta, reason=reason
            )
        ]

    if op.signal == "not_reached":
        return interests, note("ignored", "not_reached never changes memory")
    cur = find_interest(interests, op.label, op.entity)
    idx = interests.index(cur) if cur else -1

    def put(new: Interest) -> list[Interest]:
        out = list(interests)
        if idx >= 0:
            out[idx] = new
        else:
            out.append(new)
        return out

    def create(source: str, confidence: float) -> Interest:
        return Interest(
            id=interest_id(op.label, op.subtopic, op.entity),
            label=op.label,
            subtopic=op.subtopic,
            entity=op.entity,
            kind=op.kind,
            weight=0.0,
            confidence=confidence,
            source=source,
            last_reinforced_at=now,
        )

    if op.strength == "explicit":
        if op.op in ("add", "reinforce", "pin"):
            base = cur or create("explicit", cfg.explicit_confidence)
            delta = cfg.eta_explicit if (op.op != "pin" or cur is None) else 0.0
            upd = {
                "weight": base.weight + delta,
                "confidence": max(base.confidence, cfg.explicit_confidence),
                "last_reinforced_at": now,
                "evidence": _evidence(base, op, session.session_id, cfg),
            }
            if op.kind == "keep_posted":
                upd["kind"] = "keep_posted"
            if op.op == "pin":
                upd["explicit_pin"] = True
            new = base.model_copy(update=upd)
            return put(new), note(op.op if cur else f"{op.op}:new", "explicit", new, delta)
        if cur is None:
            return interests, note("ignored", f"explicit {op.op} on unknown interest")
        if op.op == "weaken":
            w = max(0.0, cur.weight - cfg.eta_explicit)
            new = cur.model_copy(
                update={"weight": w, "evidence": _evidence(cur, op, session.session_id, cfg)}
            )
            return put(new), note("weaken", "explicit", new, w - cur.weight)
        if op.op == "unpin":
            return put(cur.model_copy(update={"explicit_pin": False})), note("unpin", "explicit", cur)
        if op.op == "remove":
            return [i for i in interests if i is not cur], note("remove", "explicit", cur, -cur.weight)
        return interests, note("ignored", f"unknown op {op.op}")

    # implicit
    if op.op in ("add", "reinforce"):
        if op.signal not in POSITIVE_SIGNALS:
            return interests, note("ignored", f"implicit {op.op} with non-positive signal {op.signal}")
        s = cfg.implicit_strength.get(op.signal, cfg.implicit_strength.get("dive", 0.5))
        delta = cfg.eta_implicit * s
        base = cur or create("implicit", cfg.new_implicit_confidence)
        conf = (
            base.confidence
            if cur is None
            else min(
                cfg.implicit_confidence_cap,
                max(base.confidence, base.confidence + cfg.implicit_confidence_step),
            )
        )
        new = base.model_copy(
            update={
                "weight": base.weight + delta,
                "confidence": conf,
                "last_reinforced_at": now,
                "evidence": _evidence(base, op, session.session_id, cfg),
            }
        )
        return put(new), note(op.op if cur else f"{op.op}:new", f"implicit {op.signal} s={s}", new, delta)
    if op.op == "weaken":
        if cur is None:
            return interests, note("ignored", "implicit weaken on unknown interest")
        if not skip_rule_met(cur.id, outcome, prior, cfg, weighting):
            return interests, note(
                "skip_rule_not_met",
                f"needs skips in ≥{cfg.skip_rule.min_sessions} of last "
                f"{cfg.skip_rule.window} full-opportunity sessions",
                cur,
            )
        s = cfg.implicit_strength.get("skip", 0.2)
        w = max(0.0, cur.weight - cfg.eta_implicit * s * o)
        new = cur.model_copy(update={"weight": w, "evidence": _evidence(cur, op, session.session_id, cfg)})
        return put(new), note("weaken", f"implicit skip, o={o}", new, w - cur.weight)
    return interests, note(
        "ignored", f"implicit {op.op} is not allowed (pins and removals are explicit only)"
    )


def _naive_update(
    memory: MemorySnapshot, session: SessionRecord, today: date, o: float
) -> tuple[MemorySnapshot, MemoryUpdateReport]:
    """B5: append the user's explicit statements to the profile text. No weights, no rules."""
    turns = {t.turn_id: t for t in session.transcript}
    lines = []
    for s in session.signals:
        if not s.explicit:
            continue
        t = turns.get(s.turn_id or "")
        if t and t.role == "user":
            line = t.text.strip()
        else:
            line = (
                f"{s.type.replace('_', ' ')}: {s.interest_hint or s.payload.get('text') or s.story_ref or ''}"
            )
        line = line.strip()
        if line and line not in lines:
            lines.append(line)
    text = memory.profile_text or ""
    if lines:
        text = (text.rstrip() + "\n" if text else "") + "\n".join(f"- {line}" for line in lines)
    report = MemoryUpdateReport(
        session_id=session.session_id,
        mode="naive",
        opportunity=o,
        changes=[MemoryChange(label="profile_text", action="append", reason=line) for line in lines],
    )
    return memory.model_copy(update={"profile_text": text, "as_of": today}, deep=True), report
