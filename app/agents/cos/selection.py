"""C5 selection: slot policy + MMR (TDD §8.3). Pure, deterministic code; no model calls.

Algorithm (TDD §8.3, with the choices this module makes explicit):

1. Slot counts come from config (`slots`), resized to K by `allocate_slots`.
2. `big_news`: candidates from followed beats, base score `importance/100`, picked greedily by MMR.
3. `for_you`: all remaining candidates, base `relevance × (0.5 + 0.5·importance/100)`, MMR.
4. `outside_usual`: `adjacency == "adjacent"` candidates are preferred (no importance floor);
   only if none is available, `adjacency == "unrelated"` candidates with
   `importance ≥ min_importance_outside_usual`. Base `importance/100 × novelty`, MMR.
   `core` candidates are never eligible.
5. Unfilled slots are filled from the next best overall (for-you score, MMR) and labelled `for_you`.
6. `carryover_max` is enforced at every pick. `min_beat_share` is enforced afterwards by swapping
   the weakest non-beat picks (for-you first, then big news, outside-usual last) for the best
   unselected stories of the under-represented beat.
7. Listening order: big news → updates (grouped) → for you → outside your usual.

MMR: `λ·base − (1−λ)·max_sim(candidate, already selected)`; ties break on base score, then story_ref.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.agents.cos.config import SelectionConfig, SlotCounts
from app.agents.cos.text import jaccard
from app.contracts.brief import Adjacency, CandidateOrigin, SelectedStory, Slot

WHY_TAG: dict[Slot, str] = {
    "big_news": "big news",
    "for_you": "for you",
    "outside_usual": "outside your usual",
}
SLOT_ORDER: tuple[Slot, ...] = ("big_news", "for_you", "outside_usual")


@dataclass(frozen=True)
class ScoredItem:
    story_ref: str
    importance: int
    relevance: float = 0.0
    novelty: float = 1.0
    adjacency: Adjacency = "core"
    beat: str | None = None
    origin: CandidateOrigin = "digest"
    features: frozenset[str] = frozenset()  # tokens for redundancy (headline, entities, subtopics)
    update_of: str | None = None
    dive_likelihood: float = 0.0

    @property
    def is_update(self) -> bool:
        return self.origin == "update" or self.update_of is not None


Similarity = Callable[[ScoredItem, ScoredItem], float]


def feature_similarity(a: ScoredItem, b: ScoredItem) -> float:
    return jaccard(set(a.features), set(b.features))


def big_news_score(c: ScoredItem) -> float:
    return c.importance / 100.0


def for_you_score(c: ScoredItem) -> float:
    return c.relevance * (0.5 + 0.5 * c.importance / 100.0)


def outside_usual_score(c: ScoredItem) -> float:
    return c.importance / 100.0 * c.novelty


BASE: dict[Slot, Callable[[ScoredItem], float]] = {
    "big_news": big_news_score,
    "for_you": for_you_score,
    "outside_usual": outside_usual_score,
}


@dataclass
class SelectionResult:
    selected: list[SelectedStory]
    slot_targets: dict[str, int]
    notes: list[str] = field(default_factory=list)

    @property
    def refs(self) -> list[str]:
        return [s.story_ref for s in self.selected]


def resolve_k(k: int | None, cfg: SelectionConfig) -> int:
    return max(cfg.k_min, min(cfg.k_max, k if k is not None else cfg.k_default))


def k_from_length(length_s: float | int | None, cfg: SelectionConfig, seconds_per_story: int = 30) -> int:
    """K from the delivery preference `length_s` (about 30 s per story), clamped to [k_min, k_max]."""
    if length_s is None:
        return resolve_k(None, cfg)
    return resolve_k(int(round(float(length_s) / seconds_per_story)), cfg)


def allocate_slots(k: int, slots: SlotCounts) -> dict[Slot, int]:
    """Resize slot counts to sum to K.

    Growing adds to `for_you`. Shrinking removes from `for_you` (down to 1), then `big_news` (down to 1),
    then `outside_usual`, then the last `for_you` and `big_news`.
    """
    out: dict[Slot, int] = {
        "big_news": max(0, slots.big_news),
        "for_you": max(0, slots.for_you),
        "outside_usual": max(0, slots.outside_usual),
    }
    total = sum(out.values())
    if k > total:
        out["for_you"] += k - total
        return out
    order: list[tuple[Slot, int]] = [
        ("for_you", 1),
        ("big_news", 1),
        ("outside_usual", 0),
        ("for_you", 0),
        ("big_news", 0),
    ]
    excess = total - k
    for slot, floor in order:
        take = min(excess, max(0, out[slot] - floor))
        out[slot] -= take
        excess -= take
        if excess == 0:
            break
    return out


def _mmr_pick(
    pool: Iterable[ScoredItem],
    base: Callable[[ScoredItem], float],
    selected: list[ScoredItem],
    lam: float,
    sim: Similarity,
) -> tuple[ScoredItem, float] | None:
    best: tuple[float, float, str] | None = None
    best_item: ScoredItem | None = None
    for c in pool:
        b = base(c)
        red = max((sim(c, s) for s in selected), default=0.0)
        score = lam * b - (1 - lam) * red
        key = (score, b, _neg(c.story_ref))
        if best is None or key > best:
            best, best_item = key, c
    if best_item is None or best is None:
        return None
    return best_item, best[0]


class _neg(str):
    """Reverse string ordering so ties break on ascending story_ref inside a max()."""

    def __lt__(self, other: str) -> bool:  # type: ignore[override]
        return str.__gt__(self, other)

    def __gt__(self, other: str) -> bool:  # type: ignore[override]
        return str.__lt__(self, other)


def _eligible_big_news(c: ScoredItem, followed: set[str] | None) -> bool:
    if c.beat is None:
        return False
    return not followed or c.beat in followed


def _eligible_outside(c: ScoredItem, cfg: SelectionConfig, adjacent_only: bool) -> bool:
    if c.adjacency == "adjacent":
        return True
    if adjacent_only:
        return False
    return c.adjacency == "unrelated" and c.importance >= cfg.min_importance_outside_usual


def select(
    items: list[ScoredItem],
    cfg: SelectionConfig,
    *,
    k: int | None = None,
    followed_beats: Iterable[str] | None = None,
    similarity: Similarity = feature_similarity,
) -> SelectionResult:
    """Full slot policy (personalised arms)."""
    k = resolve_k(k, cfg)
    targets = allocate_slots(k, cfg.slots)
    followed = set(followed_beats or [])
    lam = cfg.mmr_lambda
    items = sorted({c.story_ref: c for c in items}.values(), key=lambda c: c.story_ref)  # dedupe + stable
    notes: list[str] = []

    picked: list[tuple[ScoredItem, Slot, float]] = []

    def chosen() -> list[ScoredItem]:
        return [p[0] for p in picked]

    def available(pred: Callable[[ScoredItem], bool]) -> list[ScoredItem]:
        taken = {c.story_ref for c in chosen()}
        n_carry = sum(1 for c in chosen() if c.origin == "carryover")
        return [
            c
            for c in items
            if c.story_ref not in taken
            and pred(c)
            and not (c.origin == "carryover" and n_carry >= cfg.carryover_max)
        ]

    def fill(
        slot: Slot, n: int, pred: Callable[[ScoredItem], bool], base: Callable[[ScoredItem], float]
    ) -> int:
        got = 0
        for _ in range(n):
            r = _mmr_pick(available(pred), base, chosen(), lam, similarity)
            if r is None:
                break
            picked.append((r[0], slot, r[1]))
            got += 1
        return got

    short = 0
    got = fill("big_news", targets["big_news"], lambda c: _eligible_big_news(c, followed), big_news_score)
    if got < targets["big_news"]:
        notes.append(f"big_news short by {targets['big_news'] - got}")
        short += targets["big_news"] - got
    got = fill("for_you", targets["for_you"], lambda c: True, for_you_score)
    short += targets["for_you"] - got
    n_out = targets["outside_usual"]
    if n_out:
        got = fill("outside_usual", n_out, lambda c: _eligible_outside(c, cfg, True), outside_usual_score)
        if got < n_out:
            got += fill(
                "outside_usual", n_out - got, lambda c: _eligible_outside(c, cfg, False), outside_usual_score
            )
        if got < n_out:
            notes.append(f"outside_usual short by {n_out - got}")
            short += n_out - got
    if short:
        got = fill("for_you", short, lambda c: True, for_you_score)
        if got < short:
            notes.append(f"pool exhausted: {len(picked)} of {k} stories")

    picked = _enforce_beat_share(picked, items, cfg, followed, notes)
    return SelectionResult(selected=_order(picked), slot_targets=dict(targets), notes=notes)


def select_importance_only(
    items: list[ScoredItem],
    cfg: SelectionConfig,
    *,
    k: int | None = None,
    similarity: Similarity = feature_similarity,
) -> SelectionResult:
    """B1 arm: the digest's top stories by importance (MMR), no personalisation; every pick is big news."""
    k = resolve_k(k, cfg)
    items = sorted({c.story_ref: c for c in items}.values(), key=lambda c: c.story_ref)
    picked: list[tuple[ScoredItem, Slot, float]] = []
    notes: list[str] = []
    while len(picked) < k:
        taken = {p[0].story_ref for p in picked}
        r = _mmr_pick(
            [c for c in items if c.story_ref not in taken],
            big_news_score,
            [p[0] for p in picked],
            cfg.mmr_lambda,
            similarity,
        )
        if r is None:
            notes.append(f"pool exhausted: {len(picked)} of {k} stories")
            break
        picked.append((r[0], "big_news", r[1]))
    picked = _enforce_beat_share(picked, items, cfg, set(), notes)
    return SelectionResult(
        selected=_order(picked, group_updates=False), slot_targets={"big_news": k}, notes=notes
    )


def _enforce_beat_share(
    picked: list[tuple[ScoredItem, Slot, float]],
    items: list[ScoredItem],
    cfg: SelectionConfig,
    followed: set[str],
    notes: list[str],
) -> list[tuple[ScoredItem, Slot, float]]:
    """Swap picks until each beat in `min_beat_share` has ≥ ceil(share·K) stories, where possible."""
    if not cfg.min_beat_share or not picked:
        return picked
    picked = list(picked)
    n = len(picked)
    need = {b: math.ceil(s * n - 1e-9) for b, s in sorted(cfg.min_beat_share.items())}
    slot_rank = {"for_you": 0, "big_news": 1, "outside_usual": 2}
    for beat, req in need.items():
        while sum(1 for p in picked if p[0].beat == beat) < req:
            taken = {p[0].story_ref for p in picked}
            n_carry = sum(1 for p in picked if p[0].origin == "carryover")

            def protected(c: ScoredItem, beat: str = beat) -> bool:
                # never break another constrained beat's minimum
                if c.beat in need and c.beat != beat:
                    return sum(1 for p in picked if p[0].beat == c.beat) <= need[c.beat]
                return c.beat == beat

            victims = [(i, p) for i, p in enumerate(picked) if not protected(p[0])]
            if not victims:
                notes.append(f"min_beat_share[{beat}] infeasible: no replaceable story")
                break
            vi, victim = min(victims, key=lambda ip: (slot_rank[ip[1][1]], ip[1][2], ip[1][0].story_ref))
            repl_pool = [
                c
                for c in items
                if c.beat == beat
                and c.story_ref not in taken
                and not (
                    c.origin == "carryover"
                    and n_carry - (victim[0].origin == "carryover") >= cfg.carryover_max
                )
            ]
            if not repl_pool:
                notes.append(f"min_beat_share[{beat}] infeasible: not enough {beat} candidates")
                break
            repl = max(repl_pool, key=lambda c: (for_you_score(c), c.importance, _neg(c.story_ref)))
            slot: Slot = victim[1]
            if slot == "outside_usual" or (slot == "big_news" and followed and beat not in followed):
                slot = "for_you"
            base = BASE[slot](repl)
            picked[vi] = (repl, slot, cfg.mmr_lambda * base)
            notes.append(f"min_beat_share[{beat}]: {victim[0].story_ref} -> {repl.story_ref}")
    return picked


def _order(picked: list[tuple[ScoredItem, Slot, float]], group_updates: bool = True) -> list[SelectedStory]:
    """Listening order: big news, then updates (grouped), then for you, then outside your usual.

    Within a group, the pick order (best MMR first) is kept.
    """

    def group(p: tuple[ScoredItem, Slot, float]) -> int:
        c, slot, _ = p
        if slot == "outside_usual":
            return 3
        if group_updates and c.is_update:
            return 1
        return 0 if slot == "big_news" else 2

    ordered = sorted(enumerate(picked), key=lambda ip: (group(ip[1]), ip[0]))
    return [
        SelectedStory(
            story_ref=c.story_ref, slot=slot, rank=rank, why_tag=WHY_TAG[slot], mmr_score=round(score, 6)
        )
        for rank, (_, (c, slot, score)) in enumerate(ordered, start=1)
    ]
