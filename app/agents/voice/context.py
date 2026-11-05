"""Context assembly per turn (TDD §12.2).

[system]  voice role prompt (stable)                        ┐
[system]  BRIEF (all sentences + story ids + aliases)       │ cached prefix (cache_breakpoint)
          CORPUS (source packs, compact)                    ┘
[user/assistant]  transcript so far (compact)
[user]    POSITION + open questions + the latest utterance
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.contracts.brief import BriefScript, SourcePack
from app.contracts.llm import Message
from app.speech.sentence_splitter import strip_citations
from app.tools.untrusted import wrap_untrusted

PROMPT_PATH = Path(__file__).parent / "prompts" / "voice.turn.md"
PASSAGE_CHARS = 700
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


def prefix_messages(brief: BriefScript | None, packs: list[SourcePack]) -> list[Message]:
    prompt, _ = voice_prompt()
    return [
        Message(role="system", content=prompt),
        Message(
            role="system", content=render_brief(brief) + "\n\n" + render_corpus(packs), cache_breakpoint=True
        ),
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
    msgs.append(Message(role="user", content=f"{position}{extra}\n\nListener: {utterance}"))
    return msgs
