"""Spoken-brief finishing after the claim check (C8): code rules that keep the script listenable.

Runs on the sentences that survived C7 assembly and C8, in this order:

1. **Story leads.** Every story must open with an introducing sentence. The lead is the first `story`
   sentence the writer wrote for that story (its spoken headline). If C7 or C8 removed it, the story's
   rundown teaser (cited and claim-checked like a sentence) becomes the lead ("Next: <teaser>."), with
   the teaser's citations. If the story has no checked teaser either, the story is removed: a story that
   starts mid-thought ("The run lasted about 30 hours…") is worse than one story fewer.
2. **Near-duplicates.** Adjacent `story` sentences of the same story that say the same thing (rapidfuzz
   `token_set_ratio` at or above `DUP_THRESHOLD`) keep only the longer one; a story's lead is never the one
   removed. C8 rewrites can produce these (a rewrite folds in the next sentence's fact).
3. **Rundown.** The intro's preview of the stories is built here, from the teasers of the stories that
   survived, so it never names a story the listener won't hear and the count is always right.
4. Transitions of removed stories go; the script ends with an outro.

Nothing here adds an uncited news claim: leads and the rundown are made only of checked teasers.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, utils

from app.agents.cos.write import DEFAULT_OUTRO, Teaser
from app.contracts.brief import BriefSentence, BriefStory

DUP_THRESHOLD = 85
DUP_MIN_WORDS = 4
NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


@dataclass
class Finished:
    sentences: list[BriefSentence]
    stories: list[BriefStory]
    notes: list[str]


def lead_sids(sentences: list[BriefSentence], leads: dict[str, str]) -> dict[str, str]:
    """story_ref -> sid of the writer's lead sentence, when it survived C7 assembly."""
    out: dict[str, str] = {}
    for s in sentences:
        if s.kind == "story" and s.story_ref and s.story_ref not in out:
            if s.text.strip() == leads.get(s.story_ref, "").strip():
                out[s.story_ref] = s.sid
            else:
                out.setdefault(s.story_ref, "")  # the first remaining sentence isn't the writer's lead
    return {k: v for k, v in out.items() if v}


RUNDOWN_NAMED = 3


def _number(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def rundown_sentence(stories: list[BriefStory], teasers: dict[str, Teaser]) -> BriefSentence | None:
    if not stories:
        return None
    n = len(stories)
    what = "story" if n == 1 else "stories"
    have = [teasers[st.story_ref] for st in stories if st.story_ref in teasers]
    # A spoken rundown names at most RUNDOWN_NAMED stories: six teasers in one breath sound like a list
    # (F16); the rest are heard in the brief itself.
    have = have[:RUNDOWN_NAMED]
    phrases = [t.text for t in have]
    if not phrases:
        text = f"You have {_number(n)} {what} today."
    elif len(have) == n:
        text = f"You have {_number(n)} {what} today: {_join(phrases)}."
    else:
        text = f"You have {_number(n)} {what} today, starting with {_join(phrases)}."
    cites = list(dict.fromkeys(c for t in have for c in t.cites))
    return BriefSentence(sid="rundown", text=text, kind="intro", cites=cites)


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def ensure_leads(
    sentences: list[BriefSentence],
    stories: list[BriefStory],
    leads: dict[str, str],
    teasers: dict[str, Teaser],
) -> Finished:
    notes: list[str] = []
    present = {s.sid for s in sentences}
    out = list(sentences)
    keep_stories = []
    for st in stories:
        ref = st.story_ref
        if leads.get(ref) in present:
            keep_stories.append(st)
            continue
        first = next((i for i, s in enumerate(out) if s.kind == "story" and s.story_ref == ref), None)
        if first is None:
            continue  # no sentences left at all: removed below
        t = teasers.get(ref)
        if t is None:
            notes.append(f"story {ref} lost its introducing sentence and has no checked teaser: removed")
            out = [s for s in out if s.story_ref != ref]
            continue
        prev = out[first - 1] if first > 0 else None
        if prev is not None and prev.kind == "transition" and prev.story_ref == ref:
            text = f"It's about {t.text}."
        else:
            text = f"{'First up' if not keep_stories else 'Next'}: {t.text}."
        lead = BriefSentence(
            sid=f"lead:{ref}", text=_sentence_case(text), story_ref=ref, cites=list(t.cites), kind="story"
        )
        out.insert(first, lead)
        notes.append(f"story {ref} lost its introducing sentence: lead built from its checked teaser")
        keep_stories.append(st)
    return Finished(out, keep_stories, notes)


def _content_words(text: str) -> int:
    return len(utils.default_process(text).split())


def drop_near_duplicates(
    sentences: list[BriefSentence], protected: set[str], threshold: float = DUP_THRESHOLD
) -> tuple[list[BriefSentence], list[str]]:
    """Within a story, drop a story sentence that repeats the previous story sentence of the same story."""
    notes: list[str] = []
    out: list[BriefSentence] = []
    last: dict[str, int] = {}  # story_ref -> index in `out` of its latest story sentence
    for s in sentences:
        if s.kind != "story" or not s.story_ref or s.story_ref not in last:
            if s.kind == "story" and s.story_ref:
                last[s.story_ref] = len(out)
            out.append(s)
            continue
        i = last[s.story_ref]
        prev = out[i]
        short = min(_content_words(prev.text), _content_words(s.text))
        score = fuzz.token_set_ratio(prev.text, s.text, processor=utils.default_process)
        if short < DUP_MIN_WORDS or score < threshold:
            last[s.story_ref] = len(out)
            out.append(s)
            continue
        # Keep the longer sentence, but never drop the story's lead.
        drop_prev = prev.sid not in protected and (
            s.sid in protected or _content_words(s.text) > _content_words(prev.text)
        )
        if drop_prev:
            notes.append(f"near-duplicate ({score:.0f}): dropped {prev.sid}, kept {s.sid}")
            out[i] = s
        else:
            notes.append(f"near-duplicate ({score:.0f}): dropped {s.sid}, kept {prev.sid}")
    return out, notes


def finish_script(
    sentences: list[BriefSentence],
    stories: list[BriefStory],
    leads: dict[str, str],
    teasers: dict[str, Teaser],
) -> Finished:
    """Apply the spoken-script rules above to the post-C8 sentences. `leads`: story_ref -> lead sid."""
    fin = ensure_leads(sentences, stories, leads, teasers)
    protected = set(leads.values()) | {s.sid for s in fin.sentences if s.sid.startswith("lead:")}
    sents, dup_notes = drop_near_duplicates(fin.sentences, protected)
    live = {s.story_ref for s in sents if s.kind == "story"}
    stories = [st for st in fin.stories if st.story_ref in live]
    sents = [s for s in sents if s.story_ref is None or s.story_ref in live]
    rundown = rundown_sentence(stories, teasers)
    if rundown is not None:
        intro_end = 1 if sents and sents[0].kind == "intro" else 0
        sents.insert(intro_end, rundown)
    if not sents or sents[-1].kind != "outro":
        sents.append(BriefSentence(sid="outro", text=DEFAULT_OUTRO, kind="outro"))
    return Finished(sents, stories, fin.notes + dup_notes)
