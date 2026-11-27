"""C7 write brief (`cos.write_brief`) → `BriefScript`.

The model drafts sentences; code enforces the rules it can check:
- every `story` sentence must cite at least one passage from that story's pack (invalid cites are removed;
  sentences left with none are dropped);
- intro / transition / outro sentences may not carry facts and need no citation (one that states a number
  in digits is dropped: numbers belong in cited sentences; digits inside names like "GPT-5" are fine);
- each story's rundown teaser must cite that story's passages (C8 checks it like a sentence; code builds the
  spoken rundown from the teasers of the stories that survive, see `spoken.py`);
- answered follow-ups (already researched and claim-checked) are inserted right after the intro;
- the brief ends with an open prompt (a default outro is appended if missing);
- stories left with no sentences are removed from the script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.prompts import load_prompt
from app.agents.cos.text import first_sentence, word_count
from app.agents.cos.untrusted import wrap
from app.contracts.agent_io import OpenQuestion
from app.contracts.brief import BriefSentence, BriefStory, SelectedStory, SourcePack
from app.contracts.common import BudgetExceeded
from app.contracts.memory import DeliveryPref, Interest

WORDS_PER_SECOND = 2.5
DEFAULT_OUTRO = "That's the brief. What would you like to dig into?"
# A number on its own ("24 percent", "1,000 agents", "3.5"), not digits inside a name ("GPT-5", "BWRX-300").
_NUMBER = re.compile(r"(?<![\w\-.])\d[\d,.]*(?![\w\-])")


class DraftSentence(BaseModel):
    text: str
    story_ref: str | None = None
    cites: list[str] = []
    kind: Literal["intro", "story", "transition", "outro"] = "story"


class DraftTeaser(BaseModel):
    """A short spoken phrase previewing one story in the intro rundown."""

    story_ref: str
    text: str
    cites: list[str] = []


class BriefDraft(BaseModel):
    sentences: list[DraftSentence]
    rundown: list[DraftTeaser] = []
    aliases: dict[str, list[str]] = {}


@dataclass
class Teaser:
    story_ref: str
    text: str
    cites: list[str]


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


def listener_interests(scores: dict, interests: list[Interest]) -> dict[str, list[str]]:
    """story_ref -> labels of the listener's interests the scorer matched (C4 `because`)."""
    labels = {i.id: i.label for i in interests}
    out = {}
    for ref, sc in scores.items():
        matched = [labels[b] for b in getattr(sc, "because", []) if b in labels]
        if matched:
            out[ref] = list(dict.fromkeys(matched))
    return out


def _payload(
    selected: list[SelectedStory],
    headlines: dict[str, str],
    packs: dict[str, SourcePack],
    delivery: list[DeliveryPref],
    answered: list[OpenQuestion],
    target_s: int | None,
    listener: dict[str, list[str]] | None = None,
) -> str:
    prefs = {p.key: p.value for p in delivery}
    listener = listener or {}
    if answered:
        followups = (
            "Answered follow-up questions (code inserts the answers right after your greeting; say in one "
            f"intro sentence that you'll start with them): {[q.question for q in answered]}"
        )
    else:
        followups = "Answered follow-up questions: none (do not mention follow-ups)."
    lines = [
        f"Delivery preferences: {prefs or 'none stated'}",
        f"Target length: about {target_s or 180} seconds of speech.",
        followups,
        "",
        "## Stories (in listening order)",
    ]
    for s in selected:
        pack = packs.get(s.story_ref)
        lines.append(f'### story_ref={s.story_ref} why_tag="{s.why_tag}"')
        lines.append(f"working headline: {headlines.get(s.story_ref, '')}")
        matched = (
            ", ".join(listener.get(s.story_ref, [])) or "none (don't tie it to the listener's interests)"
        )
        lines.append(f"listener interests it matches: {matched}")
        if pack:
            for f in pack.facts:
                lines.append(f"fact: {f.text} [cites: {', '.join(f.passage_ids)}]")
            for p in pack.passages:
                lines.append(wrap(p.text, id=p.passage_id, source=p.source, kind=p.kind))
            if any(p.kind == "publisher_summary" for p in pack.passages):
                # Only when such passages exist (the system prompt stays as it is, and its size with it).
                lines.append(PUBLISHER_SUMMARY_RULE)
    return "\n".join(lines)


PUBLISHER_SUMMARY_RULE = (
    'note: passages with kind="publisher_summary" are only the outlet\'s own headline and summary (the '
    "article couldn't be read). Attribute what they say to their source (\"The New York Times reports "
    'that…"), say nothing beyond their words, and keep this story to a headline and one or two sentences.'
)


def _transition_refs(sents: list[DraftSentence], known: set[str]) -> dict[int, str]:
    """Story for each transition the writer left unattributed, so it goes with its story if that story is
    removed. In a run of such transitions between two stories, the last one is the next story's signpost
    and the ones before it are the previous story's closing lines (hook, why it matters); before the first
    story they all lead into it, after the last story they all close it."""
    out: dict[int, str] = {}
    i = 0
    while i < len(sents):
        if sents[i].kind != "transition" or sents[i].story_ref in known:
            i += 1
            continue
        j = i
        while j < len(sents) and sents[j].kind == "transition" and sents[j].story_ref not in known:
            j += 1
        prv = sents[i - 1] if i > 0 else None
        nxt = sents[j] if j < len(sents) else None
        prev_ref = prv.story_ref if prv is not None and prv.story_ref in known else None
        next_ref = nxt.story_ref if nxt is not None and nxt.story_ref in known else None
        for k in range(i, j):
            ref = next_ref if (k == j - 1 or prev_ref is None) else prev_ref
            ref = ref or prev_ref
            if ref:
                out[k] = ref
        i = j
    return out


def fallback_draft(selected: list[SelectedStory], packs: dict[str, SourcePack]) -> BriefDraft:
    """Deterministic brief when the model call fails: one grounded sentence per story."""
    sents = [DraftSentence(text="Good morning. Here is your brief.", kind="intro")]
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

    transition_refs = _transition_refs(draft.sentences, set(sel))
    for i, d in enumerate(draft.sentences):
        if not d.text.strip():
            continue
        if d.kind != "story" and _NUMBER.search(d.text):
            notes.append(f"dropped uncited {d.kind} sentence with a number: {d.text[:60]!r}")
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
            ref = d.story_ref if d.story_ref in sel else transition_refs.get(i)
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


def teasers(
    draft: BriefDraft, selected: list[SelectedStory], packs: dict[str, SourcePack]
) -> dict[str, Teaser]:
    """Valid rundown teasers by story: a known story, non-empty text, cites from that story's own pack."""
    sel = {s.story_ref for s in selected}
    out: dict[str, Teaser] = {}
    for t in draft.rundown:
        text = t.text.strip().rstrip(".!?;,: ").strip()
        if t.story_ref not in sel or t.story_ref in out or not text:
            continue
        own = {p.passage_id for p in packs[t.story_ref].passages} if t.story_ref in packs else set()
        cites = [c for c in dict.fromkeys(t.cites) if c in own]
        if cites:
            out[t.story_ref] = Teaser(t.story_ref, text, cites)
    return out


def draft_leads(draft: BriefDraft) -> dict[str, str]:
    """story_ref -> the text of the first `story` sentence the writer wrote for it (its headline sentence)."""
    out: dict[str, str] = {}
    for d in draft.sentences:
        if d.kind == "story" and d.story_ref and d.story_ref not in out and d.text.strip():
            out[d.story_ref] = d.text.strip()
    return out


async def write_brief(
    cc: CallCtx,
    selected: list[SelectedStory],
    headlines: dict[str, str],
    packs: dict[str, SourcePack],
    delivery: list[DeliveryPref],
    answered: list[OpenQuestion],
    target_s: int | None,
    listener: dict[str, list[str]] | None = None,
) -> BriefDraft:
    prompt = load_prompt("cos.write_brief")
    try:
        return await call_structured(
            cc,
            prompt,
            _payload(selected, headlines, packs, delivery, answered, target_s, listener),
            BriefDraft,
            step="C7",
            max_output_tokens=4000,
        )
    except (BudgetExceeded, StructuredOutputFailure) as e:
        cc.trace.degraded("C7", f"{type(e).__name__}: deterministic fallback brief")
        return fallback_draft(selected, packs)
