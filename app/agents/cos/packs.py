"""C6 source packs (map-reduce over documents). TDD §8.2, §8.5.

For each selected story: fetch up to `docs` sources (depth by `dive_likelihood`, capped by
`fetch_per_story`), run one `cos.extract_passages` call per document (verbatim passages with character
offsets + facts), verify every passage against the document text, add community reaction (HN comments)
and related prior stories.

Passages are never invented: a passage whose text is not found verbatim in the document is dropped.
If extraction fails (budget, bad output) the document's lead is used as a verbatim passage. If fetching
fails, the search-result snippet (verbatim from the search provider) is used when one exists.

A digest story none of whose URLs gives text (paywalls, bot blocks): one search for free coverage of the
same event (`app/agents/cos/free_coverage.py`, charged to the user's search budget); an accepted hit whose
fetched text names the story's entities is extracted like any document. Still short of documents: the
publishers' own headline + feed summary (`DigestEntry.source_snippets`) become `publisher_summary`
passages, so the writer can say "The Financial Times reports that …" and C8 checks it against those words.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.agents.cos.config import CosConfig, PacksConfig
from app.agents.cos.free_coverage import (
    FOUND_VIA,
    FreeCoverageSettings,
    content_matches,
    key_entities,
    landed_ok,
    search_free_coverage,
)
from app.agents.cos.llm import CallCtx, StructuredOutputFailure, call_structured
from app.agents.cos.pool import PoolItem
from app.agents.cos.prompts import load_prompt
from app.agents.cos.text import canonical_url, hn_item_id, jaccard, short_hash, tokens
from app.agents.cos.tooling import ToolUnavailable, fetch, hn_comments, tool_ctx
from app.agents.cos.trace import RunTrace
from app.agents.cos.untrusted import sanitize, wrap
from app.common.concurrency import gather_bounded
from app.config import loader
from app.contracts.agent_io import ShownStory
from app.contracts.brief import CandidateScores, Fact, Passage, RelatedStory, SourcePack
from app.contracts.common import BudgetExceeded, Mode
from app.contracts.digest import SourceSnippet
from app.contracts.sources import Document, SearchHit
from app.contracts.tools import Tool

Depth = Literal["deep", "standard", "light"]
CHARS_PER_TOKEN = 4
LEAD_CHARS = 600
COMMENT_CHARS = 400


class ExtractedPassage(BaseModel):
    text: str
    char_start: int
    char_end: int


class ExtractedFact(BaseModel):
    text: str
    passages: list[int] = []  # indices into `passages`


class Extraction(BaseModel):
    passages: list[ExtractedPassage]
    facts: list[ExtractedFact] = []


def depth_for(dive_likelihood: float, packs: PacksConfig, prefetch: bool = True) -> Depth:
    """High dive likelihood → deep, low → light (COS-6). B4 (`prefetch=false`) uses light packs only."""
    if not prefetch:
        return "light"
    if dive_likelihood >= packs.deep_threshold:
        return "deep"
    if dive_likelihood < packs.light_threshold:
        return "light"
    return "standard"


def passage_id(doc_id: str, start: int, end: int) -> str:
    return f"{doc_id}:{start}-{end}"


def make_passage(doc: Document, start: int, end: int) -> Passage:
    return Passage(
        passage_id=passage_id(doc.url_hash, start, end),
        doc_id=doc.url_hash,
        url=doc.url,
        source=doc.source,
        published_at=doc.published_at,
        text=doc.text[start:end],
        char_start=start,
        char_end=end,
    )


def verify_offsets(text: str, p: ExtractedPassage) -> tuple[int, int] | None:
    """Return verified (start, end) for a claimed verbatim passage, or None if it is not in the text."""
    if not p.text.strip():
        return None
    s, e = p.char_start, p.char_end
    if 0 <= s < e <= len(text) and text[s:e] == p.text:
        return s, e
    idx = text.find(p.text)
    if idx >= 0:
        return idx, idx + len(p.text)
    stripped = p.text.strip()
    idx = text.find(stripped)
    if idx >= 0:
        return idx, idx + len(stripped)
    return None


def lead_span(text: str, max_chars: int = LEAD_CHARS) -> tuple[int, int] | None:
    text_len = len(text)
    if not text.strip():
        return None
    start = len(text) - len(text.lstrip())
    limit = min(text_len, start + max_chars)
    cut = max(text.rfind(". ", start, limit), text.rfind(".\n", start, limit))
    end = cut + 1 if cut > start else limit
    if end == limit and limit < text_len:
        sp = text.rfind(" ", start, limit)
        end = sp if sp > start else limit
    return start, end


def snippet_passage(hit: SearchHit) -> Passage | None:
    if not hit.snippet.strip():
        return None
    doc_id = f"snip:{hit.doc_id}"
    return Passage(
        passage_id=passage_id(doc_id, 0, len(hit.snippet)),
        doc_id=doc_id,
        url=hit.url,
        source=hit.source,
        published_at=hit.published_at,
        text=hit.snippet,
        char_start=0,
        char_end=len(hit.snippet),
    )


def publisher_passage(sn: SourceSnippet) -> Passage | None:
    """The outlet's own headline + feed summary as one verbatim passage, marked `publisher_summary`."""
    if not sn.snippet.strip():
        return None
    text = f"{sn.title}\n{sn.snippet}"
    doc_id = f"pub:{short_hash(sn.url)}"
    return Passage(
        passage_id=passage_id(doc_id, 0, len(text)),
        doc_id=doc_id,
        url=sn.url,
        source=sn.outlet or url_host(sn.url),
        published_at=sn.published_at,
        text=text,
        char_start=0,
        char_end=len(text),
        kind="publisher_summary",
    )


@lru_cache(maxsize=1)
def free_coverage_settings() -> FreeCoverageSettings:
    """Thresholds shared with story notes: `story_notes.free_coverage` in config/agent.yaml."""
    raw = (loader.agent().get("story_notes") or {}).get("free_coverage") or {}
    return FreeCoverageSettings(**raw)


async def extract_document(
    cc: CallCtx, doc: Document, *, max_chars: int, step: str, question: str | None = None
) -> tuple[list[Passage], list[Fact], bool]:
    """One `cos.extract_passages` call → verified passages and facts. Returns (passages, facts, used_llm)."""
    text = doc.text[:max_chars]
    prompt = load_prompt("cos.extract_passages")
    payload = (
        (f"Focus question: {sanitize(question)}\n\n" if question else "")
        + f"Document id {doc.url_hash}; title: {sanitize(doc.title or '')}; characters 0..{len(text)}.\n"
        + "Offsets index into the text between the tags, starting at 0 right after the opening line break.\n"
        + wrap(text, id=doc.url_hash, url=doc.url)
    )
    try:
        ext = await call_structured(cc, prompt, payload, Extraction, step=step, max_output_tokens=1500)
    except (BudgetExceeded, StructuredOutputFailure) as e:
        cc.trace.degraded(step, f"{type(e).__name__}: lead passage used", doc_id=doc.url_hash)
        span = lead_span(text)
        return ([make_passage(doc, *span)] if span else []), [], False
    passages: list[Passage] = []
    index_map: dict[int, str] = {}
    used = 0
    for n, p in enumerate(ext.passages):
        span = verify_offsets(text, p)
        if span is None:
            cc.trace.add(step, "code", ok=False, dropped_passage="not verbatim", doc_id=doc.url_hash)
            continue
        if used + (span[1] - span[0]) > max_chars:
            break
        pas = make_passage(doc, *span)
        if pas.passage_id not in {x.passage_id for x in passages}:
            passages.append(pas)
            used += span[1] - span[0]
        index_map[n] = pas.passage_id
    facts = []
    for f in ext.facts:
        ids = list(dict.fromkeys(index_map[i] for i in f.passages if i in index_map))
        if ids and f.text.strip():
            facts.append(Fact(text=f.text.strip(), passage_ids=ids))
    return passages, facts, True


def related_stories(item: PoolItem, history: list[ShownStory], limit: int = 3) -> list[RelatedStory]:
    mine = tokens(item.candidate.headline + " " + " ".join(item.entities))
    scored = []
    for h in history:
        if h.story_ref == item.ref:
            continue
        s = (
            1.0
            if h.story_ref == item.update_of
            else jaccard(mine, tokens(h.headline + " " + " ".join(h.entities)))
        )
        if s >= 0.3:
            scored.append((-s, h.shown_on, h.story_ref, h))
    scored.sort(key=lambda t: (t[0], t[2]))
    return [
        RelatedStory(story_ref=h.story_ref, headline=h.headline, shown_on=h.shown_on)
        for *_, h in scored[:limit]
    ]


@dataclass
class _UrlOutcome:
    url: str
    trace: RunTrace
    good: bool = False
    budget_exceeded: bool = False
    passages: list[Passage] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)


def url_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def domain_in(host: str, domains: Iterable[str]) -> bool:
    """True if `host` is one of `domains` or a subdomain of one (`asia.nikkei.com` in `nikkei.com`)."""
    ds = [d.lower().strip(".") for d in domains]
    return any(host == d or host.endswith("." + d) for d in ds if d)


@lru_cache(maxsize=1)
def primary_domains() -> frozenset[str]:
    """Hosts of primary sources (labs, companies, agencies; arXiv): the beats' headline sources listed
    in `newsroom.primary_sources` (config/agent.yaml) plus arXiv feeds."""
    by_beat = (loader.agent().get("newsroom") or {}).get("primary_sources") or {}
    primary_ids = {sid for ids in by_beat.values() for sid in ids}
    out = {"arxiv.org"}
    for beat in loader.beats().values():
        for s in beat.headline_sources:
            if s.url and (s.id in primary_ids or s.type == "arxiv"):
                out.add(url_host(s.url))
    return frozenset(h for h in out if h)


def order_urls(urls: Sequence[str], *, paywall: Iterable[str] = (), primary: Iterable[str] = ()) -> list[str]:
    """Fetch order for a story's URLs: open (non-paywalled) before known hard paywalls, and within each
    group primary sources first; otherwise the original order is kept (stable sort). Paywalled URLs
    are only tried last, never rewritten: no archive mirrors, no user-agent tricks."""
    paywall, primary = list(paywall), list(primary)

    def key(iu: tuple[int, str]) -> tuple[bool, bool, int]:
        host = url_host(iu[1])
        return domain_in(host, paywall), not domain_in(host, primary), iu[0]

    return [u for _, u in sorted(enumerate(urls), key=key)]


async def build_pack(
    cc: CallCtx,
    item: PoolItem,
    scores: CandidateScores | None,
    tools: dict[str, Tool],
    *,
    mode: Mode,
    now: datetime,
    cfg: CosConfig,
    history: list[ShownStory],
    concurrency: int | None = None,
) -> SourcePack:
    dive = scores.dive_likelihood if scores else 0.0
    depth = depth_for(dive, cfg.packs, cfg.flags.prefetch)
    dcfg = getattr(cfg.packs, depth)
    n_docs = max(1, min(dcfg.docs, cfg.budget.fetch_per_story))
    max_chars = dcfg.tokens * CHARS_PER_TOKEN
    per_doc_chars = max(400, max_chars // n_docs)
    urls = order_urls(
        [u for u in dict.fromkeys(item.candidate.urls) if hn_item_id(u) is None],
        paywall=cfg.packs.paywall_domains,
        primary=primary_domains(),
    )[: max(n_docs, cfg.packs.max_fetch_attempts)]
    limit = concurrency if concurrency is not None else cfg.concurrency.pack_fetches

    async def one(url: str, match: tuple[list[str], FreeCoverageSettings, str] | None = None) -> _UrlOutcome:
        """Fetch + extract one URL. `match` (free coverage): the fetched text must name the story's
        entities, and the trace records how the URL was found."""
        sub = cc.child()
        out = _UrlOutcome(url=url, trace=sub.trace)
        ctx = tool_ctx(
            mode=mode,
            now=now,
            run_id=sub.run_id,
            user_id=sub.user_id,
            budget=sub.budget,
            role="cos.extract_passages",
            step="C6",
            job_id=sub.job_id,
        )
        doc = None
        try:
            doc = await fetch(tools, ctx, url=url, max_tokens=dcfg.tokens)
            extra = {"found_via": FOUND_VIA, "match": match[2]} if match else {}
            if doc is not None and match:
                # A search redirect link counts as the publisher URL it landed on (same domain checks).
                final, why = landed_ok(url, doc.canonical_url, cfg.packs.paywall_domains, match[1])
                if why:
                    sub.trace.add("C6", "code", ok=False, url=url, rejected=why)
                    doc = None
                elif final != url:
                    doc = doc.model_copy(update={"url": final, "source": url_host(final)})
            if doc is not None and match and not content_matches(doc.text, match[0], match[1]):
                sub.trace.add("C6", "code", ok=False, url=url, rejected="page does not name the entities")
                doc = None
            sub.trace.add("C6", "tool", tool="fetch", url=url, ok=doc is not None, **extra)
        except BudgetExceeded:
            out.budget_exceeded = True
            sub.trace.degraded("C6", "fetch budget exceeded", url=url)
        except ToolUnavailable as e:
            sub.trace.add("C6", "tool", ok=False, tool="fetch", url=url, error=str(e)[:200])
            out.budget_exceeded = str(e).startswith("budget_exceeded")  # the tool's own budget refusal
        if doc is not None and doc.text.strip():
            out.good = True
            out.passages, out.facts, _ = await extract_document(sub, doc, max_chars=per_doc_chars, step="C6")
        return out

    # Fetch + extract in waves (concurrently within a wave): each wave tries as many further URLs as
    # good documents are still missing, so a failed or empty fetch (paywall, 403) moves on to the
    # story's next URL until `n_docs` good documents or the URLs (or the fetch budget) run out.
    outcomes: list[_UrlOutcome] = []
    good, pos = 0, 0
    while good < n_docs and pos < len(urls):
        wave = urls[pos : pos + n_docs - good]
        pos += len(wave)
        done = await gather_bounded([lambda u=u: one(u) for u in wave], limit)
        outcomes += done
        good += sum(o.good for o in done)
        if any(o.budget_exceeded for o in done):
            break
    # A digest story with no text from any of its URLs: one search for free coverage of the same event.
    if (
        good == 0
        and cfg.packs.free_coverage_search
        and not item.ref.startswith("u:")
        and "search" in tools
        and not any(o.budget_exceeded for o in outcomes)
    ):
        fc = free_coverage_settings()
        ctx = tool_ctx(
            mode=mode,
            now=now,
            run_id=cc.run_id,
            user_id=cc.user_id,
            budget=cc.budget,
            role="cos.free_coverage",
            step="C6",
            job_id=cc.job_id,
        )
        cs = await search_free_coverage(
            tools,
            ctx,
            headline=item.candidate.headline,
            ref_titles=[s.title for s in item.source_snippets],
            entities=item.entities,
            anchor=item.first_seen,
            now=now,
            paywall=cfg.packs.paywall_domains,
            exclude_urls=[*item.candidate.urls, *urls],
            primary=primary_domains(),
            st=fc,
        )
        cc.trace.add(
            "C6", "tool", tool="search", ok=cs.searched, story_ref=item.ref, free_coverage=cs.summary()
        )
        keys = key_entities(item.entities, item.candidate.headline)
        for v in cs.accepted[: fc.max_fetches]:
            o = await one(v.url, (keys, fc, v.reason))
            outcomes.append(o)
            if o.good:
                good += 1
                break
    # Snippet fallback (verbatim from the search provider) only for the documents still missing.
    shortfall = n_docs - good
    passages: list[Passage] = []
    facts: list[Fact] = []
    for o in outcomes:
        cc.trace.merge(o.trace)
        if o.good:
            passages += o.passages
            facts += o.facts
        elif shortfall > 0:
            hit = item.snippets.get(canonical_url(o.url))
            sp = snippet_passage(hit) if hit else None
            if sp:
                shortfall -= 1
                passages.append(sp)
                cc.trace.degraded("C6", "fetch failed: snippet passage used", url=o.url)
    # Still short: the publishers' own headline + feed summary for articles that were not fetched.
    if shortfall > 0 and cfg.packs.max_publisher_snippets > 0:
        fetched = {canonical_url(o.url) for o in outcomes if o.good}
        used = 0
        for sn in item.source_snippets:
            if shortfall <= 0 or used >= cfg.packs.max_publisher_snippets:
                break
            pp = publisher_passage(sn) if canonical_url(sn.url) not in fetched else None
            if pp is None:
                continue
            passages.append(pp)
            used += 1
            shortfall -= 1
        if used:
            cc.trace.degraded("C6", "no article text: publisher summary passages used", story_ref=item.ref)

    reaction, reaction_ids = None, []
    hn_urls = [u for u in item.candidate.urls if hn_item_id(u)]
    if cfg.flags.hn_signal and hn_urls and "hn_comments" in tools:
        item_id = hn_item_id(hn_urls[0]) or ""
        ctx = tool_ctx(
            mode=mode,
            now=now,
            run_id=cc.run_id,
            user_id=cc.user_id,
            budget=cc.budget,
            role="cos.extract_passages",
            step="C6",
            job_id=cc.job_id,
        )
        try:
            comments = await hn_comments(tools, ctx, item_id, limit=5)
        except (ToolUnavailable, BudgetExceeded) as e:
            comments = []
            cc.trace.add("C6", "tool", ok=False, tool="hn_comments", error=str(e)[:200])
        for c in comments[:5]:
            text = c.text[:COMMENT_CHARS]
            if not text.strip():
                continue
            doc_id = f"hn:{c.item_id}"
            passages.append(
                Passage(
                    passage_id=passage_id(doc_id, 0, len(text)),
                    doc_id=doc_id,
                    url=hn_urls[0],
                    source="Hacker News",
                    published_at=c.created_at,
                    text=text,
                    char_start=0,
                    char_end=len(text),
                )
            )
            reaction_ids.append(passages[-1].passage_id)
        if reaction_ids:
            reaction = f"{len(reaction_ids)} top Hacker News comments (verbatim excerpts attached)."

    return SourcePack(
        story_ref=item.ref,
        headline=item.candidate.headline,
        facts=facts,
        passages=passages,
        reaction=reaction,
        reaction_passage_ids=reaction_ids,
        related=related_stories(item, history),
        depth=depth,
    )
