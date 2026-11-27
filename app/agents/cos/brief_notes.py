"""Give the brief writer the shared story notes, and say what this listener has already heard.

Shared story notes (written once per digest story, `app/agents/newsroom/notes.py`) carry cited facts plus
background (`context`) that the C6 passages often lack. They are added to the story's source pack as
extra passages, so the writer can cite them and the claim check (C8) judges sentences against them like
any other passage:

- `note_fact`: one fact from the note, attributed to the outlet its first source came from;
- `note_context`: the note's background paragraph (may use stable background knowledge, per the note
  prompt), for "what is this, what came before" sentences.

Notes with status `no_text` add nothing. The note store is optional (`None` in replays without notes),
so a run without one behaves exactly as before.

`listener_history` gives one line per brief story: new to this listener, or a follow-up to a story they
heard on an earlier day (via the digest's `is_update_of` link to their history), or one they saved.
Exact repeats never reach the writer: the pool drops them (`pool.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

from app.contracts.agent_io import ShownStory
from app.contracts.brief import Passage, SourcePack
from app.contracts.notes import StoryNote

log = logging.getLogger(__name__)

MAX_NOTE_FACTS = 8


def _host(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def note_passages(note: StoryNote, max_facts: int = MAX_NOTE_FACTS) -> list[Passage]:
    if note.status == "no_text":
        return []
    outlets = {s.url: (s.outlet or _host(s.url)) for s in note.sources}
    doc = f"note:{note.story_id}"
    out: list[Passage] = []
    for i, f in enumerate(note.facts[:max_facts]):
        url = f.source_urls[0] if f.source_urls else (note.sources[0].url if note.sources else "")
        if not url:
            continue
        out.append(
            Passage(
                passage_id=f"n:{note.story_id}:f{i}",
                doc_id=doc,
                url=url,
                source=outlets.get(url) or _host(url),
                text=f.text,
                char_start=0,
                char_end=len(f.text),
                kind="note_fact",
            )
        )
    ctx = " ".join(t for t in (note.context.strip(), note.why_it_matters.strip()) if t)
    if ctx and note.sources:
        url = note.sources[0].url
        out.append(
            Passage(
                passage_id=f"n:{note.story_id}:ctx",
                doc_id=doc,
                url=url,
                source="background",
                text=ctx,
                char_start=0,
                char_end=len(ctx),
                kind="note_context",
            )
        )
    return out


async def add_notes_to_packs(note_store, packs: Iterable[SourcePack]) -> int:  # noqa: ANN001
    """Append note passages to each pack whose story has a shared note. Returns how many packs gained any."""
    if note_store is None:
        return 0
    n = 0
    for pk in packs:
        if pk.story_ref.startswith(("u:", "followup:")):
            continue  # per-user finds and follow-up answers have no shared note
        try:
            note = await note_store.get(pk.story_ref)
        except Exception as e:  # a notes failure must never fail the brief
            log.warning("story note for %s: %s", pk.story_ref, e)
            continue
        if note is None:
            continue
        have = {p.passage_id for p in pk.passages}
        extra = [p for p in note_passages(note) if p.passage_id not in have]
        if extra:
            pk.passages.extend(extra)
            n += 1
    return n


def listener_history(
    selected_refs: Iterable[str],
    update_of: dict[str, str | None],
    origins: dict[str, str],
    history: Iterable[ShownStory],
) -> dict[str, str]:
    """story_ref -> one line for the writer about what this listener already heard of the story."""
    shown = {h.story_ref: h for h in history}
    out: dict[str, str] = {}
    for ref in selected_refs:
        prev = shown.get(update_of.get(ref) or "")
        if origins.get(ref) == "carryover":
            out[ref] = "the listener saved this story to hear later; it is new to them"
        elif prev is not None:
            heard = "heard" if prev.heard else "was given (but may not have reached)"
            out[ref] = (
                f"follow-up: on {prev.shown_on:%A %B} {prev.shown_on.day} the listener {heard} "
                f'"{prev.headline}". '
                "Open with what is new since then, remind them in at most one clause, and skip background "
                "they already have"
            )
        else:
            out[ref] = "new to this listener: give the background they need"
    return out
