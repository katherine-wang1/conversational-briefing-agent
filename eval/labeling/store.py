"""Labelling sessions (TDD §18.3): item builders, JSONL storage, and agreement statistics.

Sessions:
  L1  topic-label spot check (100 stories) + "is this big news?" (50 stories)   - archive + labels
  L2  relevance pairs: story x simulated profile (100 pairs)                    - archive + panel
  L3  faithfulness claims vs cited passages (~120) + deep-dive answers (~80)    - dev-run outputs

Humans never see the model's label for an item (blind labelling). Labels are appended to
`eval/data/labels/<session>.jsonl`; judge labels for the same items live in
`eval/data/labels/judge_<kind>.jsonl`. `agreement()` reports accuracy and Cohen's kappa.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from eval.sim.profile import SimProfile, ai_subtopic_ids
from eval.sim.simulator import render_interests, render_latent

LABELS_DIR = Path(__file__).resolve().parents[1] / "data" / "labels"

Session = Literal["L1", "L2", "L3"]
Kind = Literal["topic", "importance", "relevance", "claim", "qa"]

OPTIONS: dict[str, list[str]] = {
    "topic": sorted(ai_subtopic_ids()) + ["other"],
    "importance": ["yes", "no", "unsure"],
    "relevance": ["1", "2", "3", "4", "5"],
    "claim": ["supported", "partial", "unsupported"],
    "qa": ["correct_supported", "incorrect", "unsupported", "abstained"],
}
ORDINAL = {"relevance"}


class LabelItem(BaseModel):
    item_id: str
    session: Session
    kind: Kind
    payload: dict[str, Any]  # what the labeller sees (never the model's label)
    options: list[str]
    meta: dict[str, Any] = {}  # hidden from the labeller (e.g. stratum, source ids)


class LabelRecord(BaseModel):
    item_id: str
    session: Session
    kind: Kind
    label: str
    labeller: str = "kate"
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---- builders ----------------------------------------------------------------------------------


def build_l1(
    stories: Sequence[Mapping[str, Any]], *, n_topic: int = 100, n_importance: int = 50, seed: int = 0
) -> list[LabelItem]:
    """`stories`: rows of labels.parquet joined with titles: {story_id, title, summary?, important_cov}.
    Importance items are stratified half important / half not, so 'yes' is not trivially rare."""
    rng = random.Random(seed)
    pool = sorted(stories, key=lambda s: str(s["story_id"]))
    topic = rng.sample(pool, min(n_topic, len(pool)))
    imp_yes = [s for s in pool if s.get("important_cov")]
    imp_no = [s for s in pool if not s.get("important_cov")]
    half = n_importance // 2
    imp = rng.sample(imp_yes, min(half, len(imp_yes))) + rng.sample(
        imp_no, min(n_importance - half, len(imp_no))
    )
    rng.shuffle(imp)
    items = [
        LabelItem(
            item_id=f"L1-topic-{s['story_id']}",
            session="L1",
            kind="topic",
            payload={"title": s["title"], "summary": s.get("summary", "")},
            options=OPTIONS["topic"],
            meta={"story_id": s["story_id"]},
        )
        for s in topic
    ]
    items += [
        LabelItem(
            item_id=f"L1-imp-{s['story_id']}",
            session="L1",
            kind="importance",
            payload={
                "title": s["title"],
                "summary": s.get("summary", ""),
                "first_seen": str(s.get("first_seen", "")),
            },
            options=OPTIONS["importance"],
            meta={"story_id": s["story_id"], "stratum": bool(s.get("important_cov"))},
        )
        for s in imp
    ]
    return items


def render_profile_for_label(p: SimProfile, day_index: int = 0) -> str:
    """Same rendering the relevance judge sees, so human and judge rate the same thing."""
    return (
        f"{p.persona}\n\nInterests (most important first):\n{render_interests(p, day_index)}\n\n"
        f"Also interested in, if it comes up:\n{render_latent(p)}"
    )


def build_l2(
    stories: Sequence[Mapping[str, Any]], profiles: Sequence[SimProfile], *, n: int = 100, seed: int = 0
) -> list[LabelItem]:
    """Story x profile pairs, balanced across profiles."""
    rng = random.Random(seed)
    pool = sorted(stories, key=lambda s: str(s["story_id"]))
    profs = sorted((p for p in profiles if not p.placeholder), key=lambda p: p.id)
    items = []
    for i in range(n):
        p = profs[i % len(profs)]
        s = rng.choice(pool)
        items.append(
            LabelItem(
                item_id=f"L2-{p.id}-{s['story_id']}-{i}",
                session="L2",
                kind="relevance",
                payload={
                    "profile": render_profile_for_label(p),
                    "headline": s["title"],
                    "summary": s.get("summary", ""),
                },
                options=OPTIONS["relevance"],
                meta={"profile_id": p.id, "story_id": s["story_id"]},
            )
        )
    return items


def build_l3(
    claims: Sequence[Mapping[str, Any]],
    answers: Sequence[Mapping[str, Any]],
    *,
    n_claims: int = 120,
    n_qa: int = 80,
    seed: int = 0,
) -> list[LabelItem]:
    """`claims`: judge-split atomic claims from dev runs: {item_id, claim, sentence, passages:[[id,text]]}.
    `answers`: deep-dive turns: {item_id, question, answer, passages:[[id,text]], answerability}."""
    rng = random.Random(seed)
    cs = rng.sample(sorted(claims, key=lambda c: c["item_id"]), min(n_claims, len(claims)))
    qs = rng.sample(sorted(answers, key=lambda a: a["item_id"]), min(n_qa, len(answers)))
    items = [
        LabelItem(
            item_id=c["item_id"],
            session="L3",
            kind="claim",
            payload={"claim": c["claim"], "sentence": c.get("sentence", ""), "passages": c["passages"]},
            options=OPTIONS["claim"],
        )
        for c in cs
    ]
    items += [
        LabelItem(
            item_id=a["item_id"],
            session="L3",
            kind="qa",
            payload={
                "question": a["question"],
                "answer": a["answer"],
                "passages": a["passages"],
                "answerability": a.get("answerability", ""),
            },
            options=OPTIONS["qa"],
        )
        for a in qs
    ]
    return items


# ---- storage -----------------------------------------------------------------------------------


def write_items(items: Iterable[LabelItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(i.model_dump_json() + "\n" for i in items))


def read_items(path: Path) -> list[LabelItem]:
    return (
        [LabelItem.model_validate_json(x) for x in path.read_text().splitlines() if x.strip()]
        if path.exists()
        else []
    )


def append_label(rec: LabelRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(rec.model_dump_json() + "\n")


def read_labels(path: Path) -> dict[str, LabelRecord]:
    """Latest label per item (a re-label overrides)."""
    out: dict[str, LabelRecord] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = LabelRecord.model_validate_json(line)
                out[r.item_id] = r
    return out


def next_unlabelled(items: Sequence[LabelItem], labels: Mapping[str, LabelRecord]) -> LabelItem | None:
    return next((i for i in items if i.item_id not in labels), None)


def read_judge_labels(path: Path) -> dict[str, str]:
    """`{"item_id": ..., "label": ...}` per line."""
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["item_id"]] = str(r["label"])
    return out


# ---- agreement ---------------------------------------------------------------------------------


def cohen_kappa(
    a: Sequence[str],
    b: Sequence[str],
    weights: Literal["none", "quadratic"] = "none",
    categories: Sequence[str] | None = None,
) -> float | None:
    """Cohen's kappa; quadratic weights for ordinal labels (categories must then be in order)."""
    if len(a) != len(b) or not a:
        return None
    cats = list(categories) if categories else sorted(set(a) | set(b))
    k = len(cats)
    idx = {c: i for i, c in enumerate(cats)}
    n = len(a)
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        obs[idx[x]][idx[y]] += 1 / n
    pa = [sum(r) for r in obs]
    pb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    if weights == "quadratic":
        w = [[((i - j) ** 2) / ((k - 1) ** 2 if k > 1 else 1) for j in range(k)] for i in range(k)]
    else:
        w = [[0.0 if i == j else 1.0 for j in range(k)] for i in range(k)]
    do = sum(w[i][j] * obs[i][j] for i in range(k) for j in range(k))
    de = sum(w[i][j] * pa[i] * pb[j] for i in range(k) for j in range(k))
    if de == 0:
        return 1.0 if do == 0 else None
    return 1 - do / de


class Agreement(BaseModel):
    kind: str
    n: int
    accuracy: float | None
    kappa: float | None
    weighted_kappa: float | None = None
    confusion: dict[str, int] = {}
    passes_bar: bool | None = None  # kappa >= 0.6 (TDD §18.3)


def agreement(kind: str, human: Mapping[str, str], judge: Mapping[str, str], bar: float = 0.6) -> Agreement:
    keys = sorted(set(human) & set(judge))
    a = [human[k] for k in keys]
    b = [judge[k] for k in keys]
    acc = sum(x == y for x, y in zip(a, b, strict=True)) / len(keys) if keys else None
    kap = cohen_kappa(a, b)
    wk = cohen_kappa(a, b, "quadratic", OPTIONS[kind]) if kind in ORDINAL and keys else None
    conf = Counter(f"{x}->{y}" for x, y in zip(a, b, strict=True))
    main = wk if wk is not None else kap
    return Agreement(
        kind=kind,
        n=len(keys),
        accuracy=acc,
        kappa=kap,
        weighted_kappa=wk,
        confusion=dict(conf),
        passes_bar=None if main is None else main >= bar,
    )


def session_paths(session: Session, root: Path = LABELS_DIR) -> tuple[Path, Path]:
    return root / f"items_{session}.jsonl", root / f"{session}.jsonl"
