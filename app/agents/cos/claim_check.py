"""C8 claim check (`cos.claim_check`): judge each sentence only against its cited passages.

Flow per batch: verdicts (+ an optional rewrite that uses only the cited passages) → rewrites are checked
once more → a rewrite judged `supported` replaces the sentence; anything else that is not `supported`
is dropped. A sentence missing from a successful response counts as unsupported. Before any model call,
a sentence that closely restates one of its passages (same words in nearly the same order, every number
and every negation of that passage kept) is accepted by code: the model check was erratic on those (it
called near-verbatim copies "partial" and then rejected its own rewrite). If the token budget
runs out, the remaining sentences are kept unchecked (they already passed the code-level citation
check) and the trace records it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel
from rapidfuzz import fuzz, utils

from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.prompts import load_prompt
from app.agents.cos.untrusted import sanitize, wrap
from app.contracts.brief import ClaimCheck, Passage
from app.contracts.common import BudgetExceeded

BATCH = 15
RESTATE_MIN = 88  # rapidfuzz token_sort_ratio for "restates the passage"
_NUM = re.compile(r"\d[\d,.]*")
_NEG = re.compile(r"\b(not|no|never|none|without|neither|nor)\b|n't", re.I)


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,").replace(",", "") for n in _NUM.findall(text)}


_HEDGE = re.compile(
    r"\b(may|might|could|would|plans?|planned|reportedly|allegedly|expected|proposed|considering|"
    r"preparing|seeking|aims?|intends?|suggests?|estimates?)\b",
    re.I,
)
_STOP = frozenset(
    "a an the of to in on at for and or by with that this is are was were be as its it s".split()
)
COVER_MIN = 0.95  # share of the sentence's content words found in one passage


def _words(text: str) -> list[str]:
    return [w for w in utils.default_process(text).split() if w not in _STOP]


def _keeps_qualifiers(text: str, p: Passage) -> bool:
    if not _numbers(text) <= _numbers(p.text):
        return False
    if len(_NEG.findall(text)) != len(_NEG.findall(p.text)):
        return False
    hedges = {h.lower() for h in _HEDGE.findall(p.text)}
    return hedges <= {h.lower() for h in _HEDGE.findall(text)}


def restates_passage(text: str, cited: list[Passage]) -> bool:
    """True when `text` restates one cited passage: a near copy, or a shortening whose words all come from
    that passage. Either way the passage's numbers, negations and hedges ("may", "plans to",
    "reportedly") must be kept, so a shortening can't turn a plan into a fact or drop a "not"."""
    words = _words(text)
    for p in cited:
        if not _keeps_qualifiers(text, p):
            continue
        if fuzz.token_sort_ratio(text, p.text, processor=utils.default_process) >= RESTATE_MIN:
            return True
        have = set(_words(p.text))
        if len(words) >= 6 and sum(w in have for w in words) / len(words) >= COVER_MIN:
            return True
    return False


class ClaimCheckBatch(BaseModel):
    checks: list[ClaimCheck]


@dataclass
class ToCheck:
    sid: str
    text: str
    cites: list[str]


@dataclass
class CheckOutcome:
    sid: str
    text: str | None  # final text, None when dropped
    record: ClaimCheck
    checked: bool = True


def _payload(batch: list[ToCheck], passages: dict[str, Passage]) -> str:
    parts = []
    for s in batch:
        cited = "\n".join(
            wrap(passages[c].text, id=c, source=passages[c].source) for c in s.cites if c in passages
        )
        parts.append(f"### sentence {s.sid}\n{sanitize(s.text)}\n#### cited passages\n{cited or '(none)'}")
    return "\n\n".join(parts)


async def _judge(
    cc: CallCtx, batch: list[ToCheck], passages: dict[str, Passage], step: str
) -> dict[str, ClaimCheck]:
    prompt = load_prompt("cos.claim_check")
    res = await call_structured(
        cc, prompt, _payload(batch, passages), ClaimCheckBatch, step=step, max_output_tokens=2048
    )
    return {c.sid: c for c in res.checks}


async def check_claims(
    cc: CallCtx, sentences: list[ToCheck], passages: dict[str, Passage], *, step: str = "C8"
) -> list[CheckOutcome]:
    out: dict[str, CheckOutcome] = {}
    budget_gone = False
    for start in range(0, len(sentences), BATCH):
        batch = sentences[start : start + BATCH]
        quick = [
            s for s in batch if restates_passage(s.text, [passages[c] for c in s.cites if c in passages])
        ]
        for s in quick:
            out[s.sid] = CheckOutcome(s.sid, s.text, ClaimCheck(sid=s.sid, verdict="supported"))
        batch = [s for s in batch if s.sid not in out]
        if not batch:
            continue
        if budget_gone:
            for s in batch:
                out[s.sid] = CheckOutcome(
                    s.sid, s.text, ClaimCheck(sid=s.sid, verdict="supported"), checked=False
                )
            continue
        try:
            verdicts = await _judge(cc, batch, passages, step)
        except (BudgetExceeded, StructuredOutputFailure) as e:
            budget_gone = isinstance(e, BudgetExceeded)
            cc.trace.degraded(
                step, f"{type(e).__name__}: sentences kept unchecked", sids=[s.sid for s in batch]
            )
            for s in batch:
                out[s.sid] = CheckOutcome(
                    s.sid, s.text, ClaimCheck(sid=s.sid, verdict="supported"), checked=False
                )
            continue
        rewrites: list[ToCheck] = []
        for s in batch:
            v = verdicts.get(s.sid) or ClaimCheck(sid=s.sid, verdict="unsupported")
            if v.verdict == "supported":
                out[s.sid] = CheckOutcome(
                    s.sid, s.text, v.model_copy(update={"rewritten": None, "dropped": False})
                )
            elif v.rewritten and v.rewritten.strip():
                rewrites.append(ToCheck(s.sid, v.rewritten.strip(), s.cites))
                out[s.sid] = CheckOutcome(s.sid, None, v.model_copy(update={"dropped": True}))
            else:
                out[s.sid] = CheckOutcome(
                    s.sid, None, v.model_copy(update={"dropped": True, "rewritten": None})
                )
        if rewrites:
            try:
                second = await _judge(cc, rewrites, passages, step)
            except (BudgetExceeded, StructuredOutputFailure) as e:
                budget_gone = isinstance(e, BudgetExceeded)
                cc.trace.degraded(
                    step, f"{type(e).__name__}: rewrites dropped", sids=[r.sid for r in rewrites]
                )
                second = {}
            for r in rewrites:
                v2 = second.get(r.sid)
                first = out[r.sid].record
                if v2 is not None and v2.verdict == "supported":
                    out[r.sid] = CheckOutcome(
                        r.sid, r.text, first.model_copy(update={"rewritten": r.text, "dropped": False})
                    )
                else:
                    out[r.sid] = CheckOutcome(
                        r.sid, None, first.model_copy(update={"rewritten": r.text, "dropped": True})
                    )
    cc.trace.add(
        step,
        "code",
        checked=len(sentences),
        restated=[
            s.sid
            for s in sentences
            if s.sid in out
            and out[s.sid].record.verdict == "supported"
            and restates_passage(s.text, [passages[c] for c in s.cites if c in passages])
        ],
        dropped=[o.sid for o in out.values() if o.text is None],
        rewritten=[o.sid for o in out.values() if o.record.rewritten and o.text is not None],
    )
    return [out[s.sid] for s in sentences]
