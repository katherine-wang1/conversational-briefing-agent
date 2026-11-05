"""C7 write brief (`cos.write_brief`) → `BriefScript`.

The model drafts sentences; code enforces the rules it can check:
- every `story` sentence must cite at least one passage from that story's pack (invalid cites are removed;
  sentences left with none are dropped);
- intro / transition / outro sentences may not carry facts and need no citation;
- answered follow-ups (already researched and claim-checked) are inserted right after the intro;
- the brief ends with an open prompt (a default outro is appended if missing);
- stories left with no sentences are removed from the script.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.prompts import load_prompt
from app.agents.cos.text import first_sentence, word_count
from app.agents.cos.untrusted import wrap
from app.contracts.agent_io import OpenQuestion
from app.contracts.brief import BriefSentence, BriefStory, SelectedStory, SourcePack
from app.contracts.common import BudgetExceeded
from app.contracts.memory import DeliveryPref

WORDS_PER_SECOND = 2.5
DEFAULT_OUTRO = "That's the brief. What would you like to dig into?"


class DraftSentence(BaseModel):
    text: str
    story_ref: str | None = None
    cites: list[str] = []
    kind: Literal["intro", "story", "transition", "outro"] = "story"


class BriefDraft(BaseModel):
    sentences: list[DraftSentence]
    aliases: dict[str, list[str]] = {}


def est_duration_s(sentences: list[BriefSentence]) -> int:
    return int(round(sum(word_count(s.text) for s in sentences) / WORDS_PER_SECOND))


def followup_sentences(answered: list[OpenQuestion]) -> list[tuple[str, list[str]]]:
    out = []
    for q in answered:
        if not q.answer or q.answer.status != "answered":
            continue
        for s in q.answer.answer_sentences:
            if s.cites:
                out.append((s.text, list(s.cites)))
    return out


def _payload(
    selected: list[SelectedStory],
    headlines: dict[str, str],
    packs: dict[str, SourcePack],
    delivery: list[DeliveryPref],
    answered: list[OpenQuestion],
    target_s: int | None,
) -> str:
    prefs = {p.key: p.value for p in delivery}
    lines = [
        f"Delivery preferences: {prefs or 'none stated'}",
        f"Target length: about {target_s or 180} seconds of speech.",
        "Answered follow-up questions (code inserts the answers after your intro; just mention that answers "
        f"come first): {[q.question for q in answered] or 'none'}",
        "",
        "## Stories (in listening order)",
    ]
    for s in selected:
        pack = packs.get(s.story_ref)
        lines.append(f'### story_ref={s.story_ref} slot={s.slot} why_tag="{s.why_tag}"')
        lines.append(f"working headline: {headlines.get(s.story_ref, '')}")
        if pack:
            for f in pack.facts:
                lines.append(f"fact: {f.text} [cites: {', '.join(f.passage_ids)}]")
            for p in pack.passages:
                lines.append(wrap(p.text, id=p.passage_id, source=p.source))
    return "\n".join(lines)


def fallback_draft(selected: list[SelectedStory], packs: dict[str, SourcePack]) -> BriefDraft:
    """Deterministic brief when the model call fails: one grounded sentence per story."""
    sents = [DraftSentence(text=f"Good morning. Here are {len(selected)} stories for today.", kind="intro")]
    for s in selected:
        pack = packs.get(s.story_ref)
        if not pack:
            continue
        if pack.facts:
            sents.append(
                DraftSentence(
                    text=pack.facts[0].text, story_ref=s.story_ref, cites=pack.facts[0].passage_ids[:1]
                )
            )
        elif pack.passages:
            p = pack.passages[0]
            src = f"{p.source} reports: " if p.source else ""
            sents.append(
                DraftSentence(text=src + first_sentence(p.text), story_ref=s.story_ref, cites=[p.passage_id])
            )
    return BriefDraft(sentences=sents)


def assemble(
    draft: BriefDraft,
    selected: list[SelectedStory],
    headlines: dict[str, str],
    packs: dict[str, SourcePack],
    followups: list[tuple[str, list[str]]],
    entities: dict[str, list[str]],
) -> tuple[list[BriefSentence], list[BriefStory], list[str]]:
    """Apply the code-checkable rules to a draft. Returns (sentences, stories, notes)."""
    notes: list[str] = []
    sel = {s.story_ref: s for s in selected}
    all_ids = {p.passage_id for pk in packs.values() for p in pk.passages}
    sents: list[BriefSentence] = []
    intro_done = False

    def add(text: str, kind: str, story_ref: str | None = None, cites: list[str] | None = None) -> None:
        sents.append(
            BriefSentence(
                sid=f"s{len(sents):03d}", text=text.strip(), story_ref=story_ref, cites=cites or [], kind=kind
            )
        )  # type: ignore[arg-type]

    def flush_followups() -> None:
        nonlocal intro_done
        if not intro_done:
            intro_done = True
            for text, cites in followups:
                add(text, "followup_answer", None, cites)

    for d in draft.sentences:
        if not d.text.strip():
            continue
        if d.kind == "intro":
            add(d.text, "intro", None, [c for c in d.cites if c in all_ids])
            continue
        flush_followups()
        if d.kind == "story":
            if d.story_ref not in sel:
                notes.append(f"dropped sentence for unknown story {d.story_ref}")
                continue
            own = {p.passage_id for p in packs[d.story_ref].passages} if d.story_ref in packs else set()
            cites = [c for c in dict.fromkeys(d.cites) if c in own]
            if not cites:
                notes.append(f"dropped uncited sentence for {d.story_ref}")
                continue
            add(d.text, "story", d.story_ref, cites)
        else:
            ref = d.story_ref if d.story_ref in sel else None
            add(d.text, d.kind, ref, [c for c in d.cites if c in all_ids])
    flush_followups()
    if not sents or sents[-1].kind != "outro":
        add(DEFAULT_OUTRO, "outro")

    stories = []
    with_text = {s.story_ref for s in sents if s.kind == "story"}
    for s in selected:
        if s.story_ref not in with_text:
            notes.append(f"story {s.story_ref} has no grounded sentences: removed from script")
            continue
        aliases = list(dict.fromkeys(draft.aliases.get(s.story_ref, []) + entities.get(s.story_ref, [])))
        stories.append(
            BriefStory(
                story_ref=s.story_ref,
                slot=s.slot,
                headline=headlines.get(s.story_ref, ""),
                why_tag=s.why_tag,
                aliases=aliases[:10],
            )
        )
    return sents, stories, notes


async def write_brief(
    cc: CallCtx,
    selected: list[SelectedStory],
    headlines: dict[str, str],
    packs: dict[str, SourcePack],
    delivery: list[DeliveryPref],
    answered: list[OpenQuestion],
    target_s: int | None,
) -> BriefDraft:
    prompt = load_prompt("cos.write_brief")
    try:
        return await call_structured(
            cc,
            prompt,
            _payload(selected, headlines, packs, delivery, answered, target_s),
            BriefDraft,
            step="C7",
            max_output_tokens=3000,
        )
    except (BudgetExceeded, StructuredOutputFailure) as e:
        cc.trace.degraded("C7", f"{type(e).__name__}: deterministic fallback brief")
        return fallback_draft(selected, packs)
