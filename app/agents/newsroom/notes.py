"""Shared story notes (docs/architecture-revisit.md, part 2): one `StoryNote` per digest story, written
once after the newsroom run and read by every user's run and by the voice agent.

Per digest entry:
1. Choose source URLs (`order_note_urls`): primary sources, then open publisher URLs, then aggregator
   redirects (Google News: the outlet is unknown until resolved), known hard paywalls last. HN item
   pages are discussion, not sources, and are left out. Paywalls are never bypassed.
2. Fetch through the `fetch` tool (its live or replay backend; the code is the same), in waves: a failed,
   empty or too-short fetch moves on to the story's next URL until `max_sources_per_story` good sources
   or `max_fetch_attempts` URLs are used. Once a redirect comes back unresolved (today's live backend
   cannot follow Google News links), the story's other redirects are skipped, not fetched.
3. One `newsroom.story_note` call over the fetched text (wrapped as untrusted content). Every fact must
   cite sources that were fetched ok (by id S1, S2, ...) and carry a short quote; code drops source ids
   that were not fetched ok, and (with `require_quotes`) facts whose quote is in none of their cited
   sources. `context` may use stable background knowledge; news claims stay cited.
4. Nothing fetched: `status="no_text"`, headline only, no model call. Fewer good sources than wanted:
   `status="partial"`. A failed model call (budget, invalid output) writes no note for that story.

Stories run concurrently (`story_notes.concurrency`); everything shares one `TokenBudget` per run.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.packs import domain_in, primary_domains, url_host
from app.agents.cos.prompts import load_prompt
from app.agents.cos.text import canonical_url, hn_item_id
from app.agents.cos.tooling import ToolUnavailable, fetch, tool_ctx
from app.agents.cos.trace import RunTrace
from app.agents.cos.untrusted import sanitize, wrap
from app.common.concurrency import gather_bounded
from app.config import loader
from app.contracts.common import BudgetExceeded, Mode, TokenBudget
from app.contracts.digest import Digest, DigestEntry
from app.contracts.llm import ModelClient
from app.contracts.notes import NoteFact, NoteSource, StoryNote
from app.contracts.sources import Document
from app.contracts.tools import Tool

PROMPT = "newsroom.story_note"
STEP = "notes"
CHARS_PER_TOKEN = 4
AGGREGATOR_HOSTS = frozenset({"news.google.com"})


# ---- settings -------------------------------------------------------------------------------------------


class NotesBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = 900_000
    output_tokens: int | None = 150_000
    fetches: int | None = 400


class StoryNotesSettings(BaseModel):
    """`story_notes:` in config/agent.yaml. Paywalled domains come from `newsroom_editor.paywalled_domains`
    unless set here."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_sources_per_story: int = 3
    max_fetch_attempts: int = 6
    concurrency: int = 6
    max_text_chars: int = 6000
    min_text_chars: int = 400
    max_output_tokens: int = 2500
    require_quotes: bool = True
    reuse_ok_hours: float = 0  # >0: an "ok" note this recent (from `existing`) is kept, not rewritten
    paywalled_domains: list[str] | None = None
    budget: NotesBudget = NotesBudget()

    @classmethod
    def from_config(cls, overrides: Mapping[str, Any] | None = None) -> StoryNotesSettings:
        cfg = loader.agent()
        raw: dict[str, Any] = dict(cfg.get("story_notes") or {})
        if overrides:
            raw = loader._deep_merge(raw, dict(overrides))
        st = cls(**raw)
        if st.paywalled_domains is None:
            st.paywalled_domains = list((cfg.get("newsroom_editor") or {}).get("paywalled_domains") or [])
        return st


# ---- model output ---------------------------------------------------------------------------------------


class DraftFact(BaseModel):
    text: str
    sources: list[str]  # source ids, e.g. ["S1"]
    quote: str = ""  # short exact phrase from a cited source


class NoteDraft(BaseModel):
    facts: list[DraftFact] = []
    entities: list[str] = []
    context: str = ""
    why_it_matters: str = ""
    open_questions: list[str] = []


# ---- URL choice -----------------------------------------------------------------------------------------


def is_aggregator(url: str) -> bool:
    return url_host(url) in AGGREGATOR_HOSTS


def order_note_urls(
    urls: Sequence[str], *, paywall: Iterable[str] = (), primary: Iterable[str] = ()
) -> list[str]:
    """Fetch order for a story's URLs: primary sources, open publisher URLs, aggregator redirects, known
    hard paywalls; within a group, one URL per outlet before a second from the same outlet; otherwise the
    digest's order. Duplicates (same canonical URL) and HN item pages are dropped."""
    paywall, primary = list(paywall), list(primary)
    seen: set[str] = set()
    per_host: dict[str, int] = {}
    keyed = []
    for i, u in enumerate(urls):
        if not u or hn_item_id(u) is not None:
            continue
        c = canonical_url(u)
        if c in seen:
            continue
        seen.add(c)
        host = url_host(u)
        if host in AGGREGATOR_HOSTS:
            group = 2
        elif domain_in(host, paywall):
            group = 3
        elif domain_in(host, primary):
            group = 0
        else:
            group = 1
        nth = per_host.get(host, 0) if group != 2 else 0  # each redirect may be a different outlet
        per_host[host] = nth + 1
        keyed.append(((group, nth, i), u))
    return [u for _, u in sorted(keyed)]


# ---- quote check ----------------------------------------------------------------------------------------

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
_SPACE = re.compile(r"\s+")


def normalise_for_match(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_QUOTE_CHARS).lower()
    return _SPACE.sub(" ", t).strip()


def quote_in(quote: str, text: str) -> bool:
    q = normalise_for_match(quote).strip(" .,;:\"'")
    return len(q) >= 8 and q in normalise_for_match(text)


# ---- one story ------------------------------------------------------------------------------------------


@dataclass
class FetchedSource:
    sid: str  # S1, S2, ... (ok sources only)
    source: NoteSource
    text: str
    title: str | None = None


@dataclass
class NoteOutcome:
    """What happened for one digest entry (the note, or why there is none)."""

    story_id: str
    note: StoryNote | None = None
    reused: bool = False
    error: str | None = None
    seconds: float = 0.0
    facts_proposed: int = 0
    facts_dropped: int = 0
    skipped_redirects: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


def _fetched_url(listed: str, doc: Document) -> str:
    """The publisher URL the document came from: `doc.url`, or its canonical URL when `doc.url` is still an
    aggregator redirect (fetch backends that resolve Google News links may report either)."""
    if is_aggregator(doc.url) and doc.canonical_url and not is_aggregator(doc.canonical_url):
        return doc.canonical_url
    return doc.url or listed


def _source_status(url: str, paywall: Iterable[str]) -> str:
    return "paywalled" if domain_in(url_host(url), paywall) else "failed"


async def _fetch_one(
    url: str, tools: Mapping[str, Tool], ctx: Any, st: StoryNotesSettings
) -> tuple[NoteSource, Document | None, bool]:
    """(source record, document if usable, budget_exhausted)."""
    paywall = st.paywalled_domains or []
    max_tokens = -(-st.max_text_chars // CHARS_PER_TOKEN)
    try:
        doc = await fetch(dict(tools), ctx, url=url, max_tokens=max_tokens)
    except BudgetExceeded as e:
        return NoteSource(url=url, status="skipped", error=f"budget: {e}"[:200]), None, True
    except ToolUnavailable as e:
        msg = str(e)
        if msg.startswith("budget_exceeded"):
            return NoteSource(url=url, status="skipped", error=msg[:200]), None, True
        return NoteSource(url=url, status=_source_status(url, paywall), error=msg[:200]), None, False
    if doc is None:
        return NoteSource(url=url, status=_source_status(url, paywall), error="no document"), None, False
    fetched = _fetched_url(url, doc)
    outlet = url_host(fetched) or doc.source
    listed = url if fetched != url else None
    text = (doc.text or "").replace(" …[truncated]", "").strip()
    if len(text) < st.min_text_chars:
        status = "failed" if is_aggregator(fetched) else _source_status(fetched, paywall)
        return (
            NoteSource(
                url=fetched, listed_url=listed, outlet=outlet, status=status, error=f"text {len(text)} chars"
            ),
            None,
            False,
        )
    doc = doc.model_copy(update={"text": text[: st.max_text_chars]})
    return NoteSource(url=fetched, listed_url=listed, outlet=outlet, status="ok"), doc, False


def render_payload(entry: DigestEntry, beat: str, sources: Sequence[FetchedSource]) -> str:
    head = (
        f"Beat: {beat}\nHeadline: {sanitize(entry.headline)}\nNewsroom summary: {sanitize(entry.summary)}\n"
        f"Entities named by the newsroom: {', '.join(sanitize(e) for e in entry.entities) or '-'}\n\n"
        f"Sources ({len(sources)}):\n"
    )
    blocks = [
        wrap(s.text, id=s.sid, url=s.source.url, outlet=s.source.outlet, title=sanitize(s.title or ""))
        for s in sources
    ]
    return head + "\n\n".join(blocks)


def validate_facts(
    draft: NoteDraft, sources: Sequence[FetchedSource], *, require_quotes: bool
) -> tuple[list[NoteFact], int]:
    """Keep facts whose cited ids are ok sources (and, with `require_quotes`, whose quote is found in one of
    them). Returns (facts, dropped)."""
    by_id = {s.sid: s for s in sources}
    by_url = {s.source.url: s for s in sources}
    out: list[NoteFact] = []
    seen: set[str] = set()
    dropped = 0
    for f in draft.facts:
        text = " ".join(f.text.split())
        cited = []
        for ref in f.sources:
            ref = ref.strip()
            s = by_id.get(ref.upper()) or by_url.get(ref)
            if s is not None and s not in cited:
                cited.append(s)
        if require_quotes:
            cited = [s for s in cited if quote_in(f.quote, s.text)]
        if not text or not cited or text.lower() in seen:
            dropped += 1
            continue
        seen.add(text.lower())
        out.append(NoteFact(text=text, source_urls=[s.source.url for s in cited]))
    return out, dropped


def _clean_list(xs: Iterable[str], limit: int) -> list[str]:
    out: list[str] = []
    for x in xs:
        x = " ".join(str(x).split())
        if x and x.lower() not in {o.lower() for o in out}:
            out.append(x)
    return out[:limit]


async def write_note(
    cc: CallCtx,
    entry: DigestEntry,
    digest: Digest,
    tools: Mapping[str, Tool],
    st: StoryNotesSettings,
    *,
    mode: Mode,
    now: datetime,
) -> NoteOutcome:
    t0 = time.monotonic()
    out = NoteOutcome(story_id=entry.story_id)
    urls = order_note_urls(entry.urls, paywall=st.paywalled_domains or [], primary=primary_domains())
    want = min(st.max_sources_per_story, len(urls))
    ctx = tool_ctx(
        mode=mode,
        now=now,
        run_id=cc.run_id,
        user_id=None,
        budget=cc.budget,
        role=PROMPT,
        step=STEP,
        job_id=cc.job_id,
    )

    records: list[NoteSource] = []
    good: list[tuple[NoteSource, Document]] = []
    seen_docs: set[str] = set()
    queue = list(urls)
    attempts, exhausted = 0, False
    # Once an aggregator redirect comes back unresolved (the fetch backend could not reach the publisher),
    # the story's other redirects would too: skip them so the attempts go to the remaining URLs.
    aggregator_dead, skipped_redirects = False, 0
    while len(good) < want and queue and attempts < st.max_fetch_attempts and not exhausted:
        wave: list[str] = []
        while queue and len(wave) < min(want - len(good), st.max_fetch_attempts - attempts):
            u = queue.pop(0)
            if aggregator_dead and is_aggregator(u):
                skipped_redirects += 1
                continue
            wave.append(u)
        if not wave:
            break
        attempts += len(wave)
        done = await gather_bounded([lambda u=u: _fetch_one(u, tools, ctx, st) for u in wave], len(wave))
        for u, (src, doc, budget_out) in zip(wave, done, strict=True):
            exhausted = exhausted or budget_out
            if is_aggregator(u) and doc is None and is_aggregator(src.url) and src.status != "skipped":
                aggregator_dead = True
            cc.trace.add(
                STEP,
                "tool",
                tool="fetch",
                url=u,
                ok=src.status == "ok",
                status=src.status,
                error=src.error,
                story_id=entry.story_id,
            )
            if doc is not None:
                key = canonical_url(src.url)
                if key in seen_docs:  # two links (e.g. redirects) to the same article
                    src = src.model_copy(update={"status": "skipped", "error": "duplicate article"})
                    doc = None
                else:
                    seen_docs.add(key)
            records.append(src)
            if doc is not None:
                good.append((src, doc))

    out.skipped_redirects = skipped_redirects
    if skipped_redirects:
        cc.trace.add(STEP, "code", story_id=entry.story_id, skipped_unresolved_redirects=skipped_redirects)
    status = "no_text" if not good else ("ok" if len(good) >= want else "partial")
    base = dict(
        story_id=entry.story_id,
        digest_id=digest.digest_id,
        beat=digest.beat,
        headline=entry.headline,
        sources=records,
        status=status,
        created_at=now,
    )
    if not good:
        cc.trace.degraded(STEP, "no source text: headline-only note", story_id=entry.story_id)
        out.note = StoryNote(**base)
        out.seconds = round(time.monotonic() - t0, 2)
        return out

    fetched = [
        FetchedSource(sid=f"S{i}", source=src, text=doc.text, title=doc.title)
        for i, (src, doc) in enumerate(good, start=1)
    ]
    prompt = load_prompt(PROMPT)
    n_events = len(cc.trace.events)
    try:
        draft = await call_structured(
            cc,
            prompt,
            render_payload(entry, digest.beat, fetched),
            NoteDraft,
            step=STEP,
            max_output_tokens=st.max_output_tokens,
        )
    except (BudgetExceeded, StructuredOutputFailure) as e:
        out.error = f"{type(e).__name__}: {e}"[:300]
        cc.trace.degraded(STEP, f"no note: {type(e).__name__}", story_id=entry.story_id)
        out.seconds = round(time.monotonic() - t0, 2)
        return out
    finally:
        for ev in cc.trace.events[n_events:]:
            if ev.kind == "llm":
                out.tokens_in += int(ev.detail.get("tokens_in") or 0)
                out.tokens_out += int(ev.detail.get("tokens_out") or 0)
    model = next(
        (ev.detail.get("model") for ev in reversed(cc.trace.events[n_events:]) if ev.kind == "llm"), None
    )
    facts, dropped = validate_facts(draft, fetched, require_quotes=st.require_quotes)
    out.facts_proposed, out.facts_dropped = len(draft.facts), dropped
    if dropped:
        cc.trace.add(STEP, "code", ok=False, story_id=entry.story_id, dropped_facts=dropped)
    out.note = StoryNote(
        **base,
        facts=facts,
        entities=_clean_list(draft.entities, 12),
        context=" ".join(draft.context.split()),
        why_it_matters=" ".join(draft.why_it_matters.split()),
        open_questions=_clean_list(draft.open_questions, 5),
        model=model,
        prompt_version=prompt.version,
    )
    out.seconds = round(time.monotonic() - t0, 2)
    return out


# ---- a whole digest -------------------------------------------------------------------------------------


@dataclass
class NotesRun:
    digest_id: str
    beat: str
    outcomes: list[NoteOutcome]
    trace: RunTrace
    budget: TokenBudget
    seconds: float = 0.0
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def notes(self) -> list[StoryNote]:
        """Notes written in this run (reused ones excluded: they are already stored)."""
        return [o.note for o in self.outcomes if o.note is not None and not o.reused]

    def summary(self) -> dict[str, Any]:
        written = self.notes
        status: dict[str, int] = {}
        for n in written:
            status[n.status] = status.get(n.status, 0) + 1
        src: dict[str, int] = {}
        for n in written:
            for s in n.sources:
                src[s.status] = src.get(s.status, 0) + 1
        with_text = [n for n in written if n.status != "no_text"]
        return {
            "digest_id": self.digest_id,
            "beat": self.beat,
            "entries": len(self.outcomes),
            "written": len(written),
            "reused": sum(o.reused for o in self.outcomes),
            "no_note": [o.story_id for o in self.outcomes if o.note is None],
            "status": status,
            "sources": src,
            "facts": sum(len(n.facts) for n in written),
            "facts_dropped": sum(o.facts_dropped for o in self.outcomes),
            "facts_per_note_with_text": round(sum(len(n.facts) for n in with_text) / len(with_text), 2)
            if with_text
            else 0.0,
            "tokens_in": self.budget.used_input_tokens,
            "tokens_out": self.budget.used_output_tokens,
            "fetches": self.budget.used_fetches,
            "seconds": self.seconds,
        }


async def write_story_notes(
    digest: Digest,
    model_client: ModelClient,
    tools: Mapping[str, Tool],
    *,
    settings: StoryNotesSettings | None = None,
    mode: Mode = "live",
    now: datetime | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    cache_namespace: str | None = None,
    budget: TokenBudget | None = None,
    limit: int | None = None,
    existing: Any = None,  # NoteStore: with `reuse_ok_hours`, fresh "ok" notes are kept
) -> NotesRun:
    """Write a note for each entry of `digest` (in digest order, at most `limit`). Never raises for one
    story's failure; see `NoteOutcome.error`."""
    st = settings or StoryNotesSettings.from_config()
    when = now or (digest.at if mode == "replay" else datetime.now(UTC))
    rid = run_id or digest.run_id
    b = budget or TokenBudget(**st.budget.model_dump())
    trace = RunTrace(run_id=rid, job="story_notes")
    cc = CallCtx(
        client=model_client,
        budget=b,
        trace=trace,
        run_id=rid,
        job_id=job_id,
        cache_namespace=cache_namespace or rid,
    )
    entries = list(digest.entries)[: limit if limit is not None else None]
    t0 = time.monotonic()

    async def one(entry: DigestEntry) -> tuple[NoteOutcome, RunTrace]:
        sub = cc.child()
        if existing is not None and st.reuse_ok_hours > 0:
            prior = await existing.get(entry.story_id)
            if (
                prior is not None
                and prior.status == "ok"
                and prior.created_at >= when - timedelta(hours=st.reuse_ok_hours)
            ):
                sub.trace.add(STEP, "code", story_id=entry.story_id, reused=prior.digest_id)
                return NoteOutcome(story_id=entry.story_id, note=prior, reused=True), sub.trace
        return await write_note(sub, entry, digest, tools, st, mode=mode, now=when), sub.trace

    results = await gather_bounded([lambda e=e: one(e) for e in entries], max(1, st.concurrency))
    outcomes = []
    for o, tr in results:  # merged in digest order, whatever finished first
        trace.merge(tr)
        outcomes.append(o)
    return NotesRun(
        digest_id=digest.digest_id,
        beat=digest.beat,
        outcomes=outcomes,
        trace=trace,
        budget=b,
        seconds=round(time.monotonic() - t0, 2),
        settings=st.model_dump(),
    )


__all__ = [
    "NoteDraft",
    "NoteOutcome",
    "NotesRun",
    "StoryNotesSettings",
    "order_note_urls",
    "quote_in",
    "validate_facts",
    "write_note",
    "write_story_notes",
]
