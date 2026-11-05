"""T3 curated deep-dive QA set builder (TDD §18.1, PRD §10.2).

Builds candidate questions from *real* source packs produced by agent runs (dev slice first), with an
LLM (role `eval.qgen`) proposing questions and answerability labels. Code then validates every item
(answerable items must quote their supporting passage verbatim) and marks all of them
`human_checked: false`; ~50 are human-checked in labelling session L3. Nothing is generated until the
archive and dev runs exist - this module never fabricates the set.

  python -m eval.tasks.t3_qgen --run dev-full-s1 --client pkg.mod:factory --per-pack 3 \
      --max-packs 85 --out eval/data/t3_curated_v1.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import random
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.brief import SourcePack
from app.contracts.common import TraceCtx
from app.contracts.llm import ModelClient
from eval.runner.modelcall import call_structured, load_prompt
from eval.runner.traces import RUNS_DIR, load_day_records

PROMPT = Path(__file__).resolve().parent / "prompts" / "t3_qgen.md"
Label = Literal["answerable", "needs_search", "unanswerable"]


class GenQuestion(BaseModel):
    question: str
    label: Label
    supporting_passage_id: str | None = None
    supporting_quote: str | None = None
    rationale: str = Field(default="", max_length=400)


class GenOutput(BaseModel):
    questions: list[GenQuestion]


class T3Item(BaseModel):
    qid: str
    run_id: str
    brief_id: str
    story_ref: str
    question: str
    label: Label
    supporting_passage_id: str | None = None
    supporting_quote: str | None = None
    supporting_text: str | None = None  # the full passage, for the QA judge
    label_source: str = "llm:eval.qgen"
    prompt_sha: str = ""
    model: str = ""
    human_checked: bool = False
    human_label: Label | None = None


def validate(q: GenQuestion, pack: SourcePack) -> str | None:
    """Return a rejection reason, or None if the item is usable."""
    if len(q.question.split()) < 3:
        return "question too short"
    passages = {p.passage_id: p for p in pack.passages}
    if q.label == "answerable":
        p = passages.get(q.supporting_passage_id or "")
        if p is None:
            return "answerable item without a valid passage id"
        if not q.supporting_quote or q.supporting_quote.strip() not in p.text:
            return "supporting quote is not verbatim in the passage"
    elif q.supporting_passage_id:
        return f"{q.label} item must not cite a passage"
    return None


def render(pack: SourcePack, n: int) -> dict[str, str]:
    mix = {1: "1 answerable", 2: "1 answerable, 1 unanswerable"}.get(
        n, f"{n - 2} answerable, 1 needs_search, 1 unanswerable"
    )
    return {
        "headline": pack.headline,
        "n": str(n),
        "mix": mix,
        "facts": "\n".join(f"- {f.text}" for f in pack.facts) or "(none)",
        "passages": "\n\n".join(f"[{p.passage_id}] ({p.source or p.url})\n{p.text}" for p in pack.passages),
    }


async def generate_for_pack(
    client: ModelClient,
    pack: SourcePack,
    *,
    run_id: str,
    brief_id: str,
    n: int = 3,
    cache_namespace: str = "t3-qgen-v1",
) -> tuple[list[T3Item], list[str]]:
    prompt = load_prompt(PROMPT)
    system, user = prompt.render(**render(pack, n))
    out, resp = await call_structured(
        client,
        role=prompt.role,
        system=system,
        user=user,
        schema=GenOutput,
        trace=TraceCtx(run_id="t3-qgen", role=prompt.role, step=pack.story_ref),
        cache_namespace=f"{cache_namespace}:{prompt.sha}",
        temperature=0.3,
    )
    items, rejects = [], []
    by_id = {p.passage_id: p for p in pack.passages}
    for i, q in enumerate(out.questions):
        why = validate(q, pack)
        if why:
            rejects.append(f"{pack.story_ref}#{i}: {why}")
            continue
        items.append(
            T3Item(
                qid=f"t3-{brief_id}-{pack.story_ref}-{i}",
                run_id=run_id,
                brief_id=brief_id,
                story_ref=pack.story_ref,
                question=q.question,
                label=q.label,
                supporting_passage_id=q.supporting_passage_id,
                supporting_quote=q.supporting_quote,
                supporting_text=by_id[q.supporting_passage_id].text if q.supporting_passage_id else None,
                prompt_sha=prompt.sha,
                model=resp.model,
            )
        )
    return items, rejects


def select_packs(
    run_id: str, runs_dir: Path = RUNS_DIR, max_packs: int = 85, seed: int = 0
) -> list[tuple[str, SourcePack]]:
    """(brief_id, pack) pairs from a run's traces, deduplicated by story, sampled deterministically."""
    seen, pool = set(), []
    for _, rec in sorted(load_day_records(runs_dir, run_id).items(), key=lambda kv: (kv[0][0], kv[0][1])):
        for p in rec.packs:
            if p.story_ref not in seen and p.passages:
                seen.add(p.story_ref)
                pool.append((rec.brief.brief_id, p))
    rng = random.Random(seed)
    return rng.sample(pool, min(max_packs, len(pool)))


async def build(
    client: ModelClient,
    packs: Sequence[tuple[str, SourcePack]],
    *,
    run_id: str,
    n: int = 3,
    concurrency: int = 4,
) -> tuple[list[T3Item], list[str]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(bid: str, p: SourcePack) -> tuple[list[T3Item], list[str]]:
        async with sem:
            return await generate_for_pack(client, p, run_id=run_id, brief_id=bid, n=n)

    res = await asyncio.gather(*(one(b, p) for b, p in packs))
    return [i for r in res for i in r[0]], [x for r in res for x in r[1]]


def write_items(items: Iterable[T3Item], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(i.model_dump_json() + "\n" for i in items))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.tasks.t3_qgen")
    ap.add_argument("--run", required=True, help="run id whose source packs to use (dev slice)")
    ap.add_argument("--client", required=True, help="module:factory returning a ModelClient")
    ap.add_argument("--per-pack", type=int, default=3)
    ap.add_argument("--max-packs", type=int, default=85)
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    mod, _, name = a.client.partition(":")
    client = getattr(importlib.import_module(mod), name)()
    packs = select_packs(a.run, a.runs_dir, a.max_packs)
    if not packs:
        print(f"no source packs in run {a.run}", file=sys.stderr)
        return 1
    items, rejects = asyncio.run(build(client, packs, run_id=a.run, n=a.per_pack))
    write_items(items, a.out)
    print(json.dumps({"packs": len(packs), "items": len(items), "rejected": len(rejects)}, indent=2))
    for r in rejects[:20]:
        print("rejected:", r, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
