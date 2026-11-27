"""Context assembly per turn (TDD §12.2).

[system]  voice role prompt (stable)                        ┐
[system]  BRIEF (all sentences + story ids + aliases)       │ cached prefix (cache_breakpoint)
          STORED STORIES (today's wider set, one line each) │
          CORPUS (source packs, compact)                    ┘
[user/assistant]  transcript so far (compact)
[user]    POSITION + open questions + the latest utterance
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.contracts.brief import BriefScript, SourcePack
from app.contracts.llm import Message
from app.contracts.notes import StoredStory, StoryNote, UserStorySet
from app.speech.sentence_splitter import strip_citations
from app.tools.untrusted import wrap_untrusted

PROMPT_PATH = Path(__file__).parent / "prompts" / "voice.turn.md"
PASSAGE_CHARS = 700
STORED_LINE_CHARS = 140
TRANSCRIPT_TURNS = 12


@lru_cache
def voice_prompt() -> tuple[str, str]:
    """(prompt text, version) from the front-matter file."""
    raw = PROMPT_PATH.read_text()
    version = "0"
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        for line in fm.splitlines():
            if line.startswith("version:"):
                version = line.split(":", 1)[1].strip()
        raw = body
    return raw.strip(), version


def render_brief(brief: BriefScript | None) -> str:
    if brief is None:
        return "BRIEF: (no brief today)"
    lines = ["BRIEF", "Stories:"]
    for i, st in enumerate(brief.stories, 1):
        alias = f" aliases: {', '.join(st.aliases)}" if st.aliases else ""
        lines.append(f"  {i}. story_id={st.story_ref} [{st.why_tag}] {st.headline}{alias}")
    lines.append("Script (segment id | story | text):")
    for s in brief.sentences:
        text, cites = strip_citations(s.text)
        c = f" cites={','.join(s.cites or cites)}" if (s.cites or cites) else ""
        lines.append(f"  {s.sid} | {s.story_ref or '-'} | {text}{c}")
    return "\n".join(lines)


def _one_line(text: str, n: int) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def render_stored(story_set: UserStorySet | None) -> str:
    """L1: one line per stored story. Detail for any of them comes from `get_story` (L2)."""
    if story_set is None or not story_set.stories:
        return ""
    lines = ["STORED STORIES (today's wider set; [in brief] = covered by the spoken brief)"]
    for s in story_set.stories:
        flag = "[in brief] " if s.in_brief else ""
        line = f"  story_id={s.story_ref} {flag}{_one_line(s.headline, 120)}"
        if s.summary:
            line += f" — {_one_line(s.summary, STORED_LINE_CHARS)}"
        lines.append(line)
    return "\n".join(lines)


def note_cite_id(story_ref: str, i: int) -> str:
    return f"note:{story_ref}:{i}"


def stored_cite_id(story_ref: str) -> str:
    return f"stored:{story_ref}"


def render_note(note: StoryNote) -> str:
    """get_story result when a shared note exists: cited facts plus context, wrapped as untrusted
    (it was written from fetched web text). Fact cite ids are `note:<story_id>:<n>`."""
    out = [f"Headline: {note.headline}"]
    for i, f in enumerate(note.facts):
        out.append(f"- fact: {f.text} [p:{note_cite_id(note.story_id, i)}]")
    if note.entities:
        out.append("Involved: " + ", ".join(note.entities[:12]))
    if note.context:
        out.append(f"Context: {note.context}")
    if note.why_it_matters:
        out.append(f"Why it matters: {note.why_it_matters}")
    for q in note.open_questions[:5]:
        out.append(f"- not known yet: {q}")
    if note.status != "ok":
        out.append(f"(note status: {note.status}; some sources could not be read)")
    return wrap_untrusted("\n".join(out), tool="get_story", id=note.story_id)


def render_stored_fallback(story: StoredStory, *, in_corpus: bool = False) -> str:
    """get_story result when no note is stored: only the headline, summary and links (plus a pointer
    to the CORPUS when this run built a source pack for the story)."""
    body = [
        f"Headline: {story.headline}",
        f"Summary: {story.summary or '(none)'} [p:{stored_cite_id(story.story_ref)}]",
        "Sources: " + (", ".join(story.urls[:3]) or "(none)"),
    ]
    if in_corpus:
        body.append(
            f"No detailed note is stored, but the CORPUS has passages for story_id={story.story_ref}:"
            " answer from those first."
        )
    else:
        body.append(
            "No detailed note is stored for this story. Say only what the headline and summary support;"
            " for more, call quick_search or offer to bookmark the question."
        )
    return wrap_untrusted("\n".join(body), tool="get_story", id=story.story_ref)


def render_corpus(packs: list[SourcePack]) -> str:
    if not packs:
        return "CORPUS: (empty)"
    out = ["CORPUS"]
    for p in packs:
        out.append(f"## story_id={p.story_ref}: {p.headline}")
        for f in p.facts:
            out.append(f"- fact: {f.text} [{', '.join('p:' + x for x in f.passage_ids)}]")
        if p.reaction:
            out.append(f"- reaction: {p.reaction} [{', '.join('p:' + x for x in p.reaction_passage_ids)}]")
        for r in p.related:
            out.append(f"- related earlier: {r.headline} ({r.shown_on or 'earlier'})")
        for ps in p.passages:
            out.append(
                wrap_untrusted(
                    ps.text[:PASSAGE_CHARS],
                    tool="source_pack",
                    id=ps.passage_id,
                    url=ps.url,
                    source=ps.source,
                )
            )
    return "\n".join(out)


def prefix_messages(
    brief: BriefScript | None, packs: list[SourcePack], story_set: UserStorySet | None = None
) -> list[Message]:
    prompt, _ = voice_prompt()
    parts = [render_brief(brief), render_stored(story_set), render_corpus(packs)]
    return [
        Message(role="system", content=prompt),
        Message(role="system", content="\n\n".join(p for p in parts if p), cache_breakpoint=True),
    ]


def position_line(brief: BriefScript | None, segment_id: str | None, completed: bool) -> str:
    if brief is None:
        return "POSITION: no brief."
    if completed:
        return "POSITION: the brief has finished; this is open conversation."
    if segment_id is None:
        return "POSITION: the brief hasn't started yet."
    sids = [s.sid for s in brief.sentences]
    if segment_id not in sids:
        return f"POSITION: segment {segment_id}."
    sent = brief.sentences[sids.index(segment_id)]
    if sent.story_ref is None:
        return f"POSITION: interrupted during segment {segment_id} ({sent.kind}), not inside a story."
    refs = [st.story_ref for st in brief.stories]
    n = refs.index(sent.story_ref) + 1 if sent.story_ref in refs else "?"
    head = next((st.headline for st in brief.stories if st.story_ref == sent.story_ref), "")
    in_story = [s.sid for s in brief.sentences if s.story_ref == sent.story_ref]
    k = in_story.index(segment_id) + 1
    return (
        f"POSITION: user interrupted during story {n} (story_id={sent.story_ref}, '{head}'), "
        f"sentence {k} (segment {segment_id})."
    )


def turn_messages(
    prefix: list[Message],
    history: list[tuple[str, str]],
    position: str,
    utterance: str,
    open_questions: str = "",
) -> list[Message]:
    msgs = list(prefix)
    for role, text in history[-TRANSCRIPT_TURNS:]:
        msgs.append(Message(role="user" if role == "user" else "assistant", content=text))
    extra = f"\nOPEN QUESTIONS: {open_questions}" if open_questions else ""
    msgs.append(Message(role="user", content=f"{position}{extra}\n\nListener: {utterance}{SPOKEN_REMINDER}"))
    return msgs


# Haiku-class models with reasoning off sometimes "think out loud" in the answer (live run,
# docs/smoke/2026-11-06/web-e2e): every sentence is spoken, so remind them on each turn, and drop
# sentences that are clearly narration about the listener or the prompt (is_meta_sentence).
SPOKEN_REMINDER = "\n\n(Reply with only the words you say to the listener, addressed as you.)"

_META = re.compile(
    r"^(?:the (?:listener|user) (?:is|was|wants|asked|asks|means|might|seems|probably)\b"
    r"|they(?:'re| are) asking\b|their (?:position|question) is\b"
    r"|i should (?:answer|respond|reply|say|note|mention|ask|clarify|call|use|explain)\b"
    r"|i(?:'ll| will) answer (?:from|based)\b"
    r"|the (?:deal|story|company|one|question) they mean\b)"
    r"|\b(?:most (?:likely|plausibly)|plausibly) refers to\b|\bposition line\b|\bstory_id\b",
    re.IGNORECASE,
)
_CORPUS = re.compile(r"\bthe corpus\b", re.IGNORECASE)


def is_meta_sentence(text: str) -> bool:
    """True for a sentence that narrates the model's reasoning instead of talking to the listener."""
    return bool(_META.search(text.strip()))


def spoken_form(text: str) -> str:
    """Replace the prompt's word "the corpus" with "what I have" before it is spoken."""
    return _CORPUS.sub(lambda m: "What I have" if m.group(0)[0] == "T" else "what I have", text)
