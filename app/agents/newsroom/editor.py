"""Editor newsroom: a wide, cheap candidate list ranked by one strong-model "editor" call.

An alternative to the classic N2-N6 pipeline (pipeline.py), selected with `newsroom.impl: editor`
(see `app/agents/newsroom/__init__.py`). Same signature and return type as the classic
`run_newsroom`, so callers don't change. Steps:

- **N2 normalise**: run-window filter (as classic), freshness rule (`freshness.py`: stale feed items
  and long undated feed archives are dropped), then dedupe by canonical URL (classic `normalize`).
- **N3 dedupe**: exact / near-duplicate titles (rapidfuzz) merge items into candidates. No embeddings
  (fastembed is never imported on this path).
- **N4 candidates**: a simple source signal (source weight, front-page rank, coverage, HN points)
  orders candidates; the weakest beyond `max_candidates` are dropped.
- **N5 editor**: one `newsroom.editor` call over all candidates (or 2-3 calls on contiguous slices of
  the signal order, merged by headline overlap) returns stories: groups of candidate ids that are
  the same event, a headline, summary, subtopics, entities, importance 0-100 and evidence.
- **N6 digest**: story identity across runs (shared URLs, else rapidfuzz title similarity against
  `history`), code-computed features and `importance_rules` (as classic), digest entries.

No tools are used (the `tools` argument is accepted and ignored): the only external call is the
model call, so the same code runs in replay on archived observations (TOOL-1). No user data
(NEWS-2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from rapidfuzz import fuzz, process

from app.agents.newsroom.features import (
    SourceIndex,
    compute_features,
    importance_rules,
    rules_evidence,
)
from app.agents.newsroom.freshness import FRONTPAGE_TYPES, freshness_filter
from app.agents.newsroom.normalize import (
    NormItem,
    clean_text,
    normalize,
    registrable_domain,
    strip_invisible,
    title_key,
)
from app.agents.newsroom.prompts import Prompt, load_prompt
from app.agents.newsroom.settings import Settings
from app.agents.newsroom.stories import story_id_for
from app.agents.newsroom.trace import LlmCallRecord, NewsroomTrace, Trace, TraceStep
from app.config import loader
from app.config.loader import BeatCfg, ExperimentFlags
from app.contracts.common import BudgetExceeded, Mode, Strict, TokenBudget, TraceCtx
from app.contracts.digest import Digest, DigestEntry, DigestEntryLLM, Story, StoryFeatures
from app.contracts.llm import ChatRequest, ChatResponse, Message, ModelClient, StructuredOutputInvalid
from app.contracts.sources import HeadlineObservation
from app.contracts.tools import SearchResult, Tool, ToolContext

EDITOR_ROLE = "newsroom.editor"
EDITOR_PROMPT = "newsroom.editor"
EMBEDDER_NAME = "none (editor)"
GOOGLE_NEWS_HOSTS = {"news.google.com"}


# ---- settings -------------------------------------------------------------------------------------


class EditorSettings(BaseModel):
    """config/agent.yaml `newsroom_editor` (hashed into Digest.version)."""

    model_config = ConfigDict(extra="forbid")

    max_undated_per_source: int = 20
    title_dedupe_threshold: float = 90
    max_candidates: int = 400
    chunk_size: int = 400
    max_calls: int = 3
    max_stories: int = 40
    min_stories: int = 30  # ask for at least a digest's worth (beat digest_size is 30)
    max_output_tokens: int = 10000
    # Prompt file under app/agents/prompts/ (a variant, e.g. newsroom.editor_v3, is a config change).
    prompt: str = EDITOR_PROMPT
    snippet_chars: int = 160
    merge_title_threshold: float = 88
    identity_title_threshold: float = 88
    paywalled_domains: list[str] = []
    # Gap-fill search (docs/architecture-revisit.md, arm `editor_search`): fixed per-beat queries run
    # through the `search` tool before N2, so the live backend (e.g. Anthropic web search) and the
    # replay backend (archive) are interchangeable (TOOL-1). Queries carry no user context (NEWS-2).
    gap_search: bool = False
    gap_queries: dict[str, list[str]] = {}
    gap_max_searches: int = 4
    gap_max_results: int = 8

    @classmethod
    def from_config(cls, overrides: Mapping[str, Any] | None = None) -> EditorSettings:
        raw = dict(loader.agent().get("newsroom_editor", {}) or {})
        if overrides:
            raw = loader._deep_merge(raw, dict(overrides))
        return cls(**raw)


# ---- gap-fill search ------------------------------------------------------------------------------

GAP_SOURCE_ID = "gap_search"


def _search_tool(tools: Mapping[str, Tool] | Sequence[Tool] | None) -> Tool | None:
    if not tools:
        return None
    if isinstance(tools, Mapping):
        return tools.get("search")
    return next((t for t in tools if getattr(t, "name", None) == "search"), None)


async def gap_search_observations(
    beat_id: str,
    beat_name: str,
    at: datetime,
    tools: Mapping[str, Tool] | Sequence[Tool] | None,
    es: EditorSettings,
    *,
    mode: Mode,
    run_id: str,
    job_id: str | None,
    window_hours: int,
) -> tuple[list[HeadlineObservation], dict[str, Any]]:
    """Run the beat's fixed gap-fill queries through the `search` tool; hits become observations."""

    tool = _search_tool(tools)
    default = [f"most important {beat_name} news today"]
    queries = (es.gap_queries.get(beat_id) or default)[: es.gap_max_searches]
    info: dict[str, Any] = {"queries": queries, "hits": 0, "errors": []}
    if tool is None:
        info["errors"].append("no search tool")
        return [], info
    budget = TokenBudget(input_tokens=0, searches=len(queries))
    since = at - timedelta(hours=window_hours)

    async def one(q: str) -> list[HeadlineObservation]:
        ctx = ToolContext(
            mode=mode, now=at, run_id=run_id, user_id=None, budget=budget,
            trace=TraceCtx(run_id=run_id, user_id=None, job_id=job_id, role="newsroom", step="N1b"),
        )
        res = await tool(ctx, query=q, since=since, until=at, max_results=es.gap_max_results)
        if not res.ok:
            info["errors"].append(f"{q}: {res.error}")
            return []
        data = res.data
        hits = data.hits if hasattr(data, "hits") else SearchResult.model_validate(data).hits
        return [
            HeadlineObservation(
                source_id=GAP_SOURCE_ID, beat=beat_id, observed_at=at, url=h.url, title=h.title,
                rank=i, snippet=h.snippet or None, published_at=h.published_at, provenance="search",
                extra={"query": q, "outlet": h.source},
            )
            for i, h in enumerate(hits, 1)
        ]

    results = await asyncio.gather(*(one(q) for q in queries))
    obs = [o for r in results for o in r]
    info["hits"] = len(obs)
    return obs, info


# ---- structured output ----------------------------------------------------------------------------


class EditorStoryOut(Strict):
    ids: list[str]
    headline: str
    summary: str
    subtopics: list[str] = []
    entities: list[str] = []
    importance: int
    evidence: list[str] = []


class EditorOut(Strict):
    stories: list[EditorStoryOut]


# ---- candidates -----------------------------------------------------------------------------------


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return (m.group(1) if m else "").lower()


def is_google_news(url: str) -> bool:
    return _host(url) in GOOGLE_NEWS_HOSTS


def item_outlet(it: NormItem) -> str:
    """Who published the item: the feed's <source> name (Google News), else the domain."""
    for o in it.observations:
        name = (o.extra or {}).get("outlet")
        if name:
            return str(name)
    return it.domain


def fix_redirect_domains(items: Sequence[NormItem]) -> None:
    """Google News links are redirects (domain google.com): use the outlet's own domain instead, so
    coverage counts outlets, not one aggregator. Mutates `items` (editor-local NormItems)."""
    for it in items:
        if not is_google_news(it.url):
            continue
        dom = None
        for o in it.observations:
            ex = o.extra or {}
            if ex.get("outlet_url"):
                dom = registrable_domain(str(ex["outlet_url"]))
                break
            if ex.get("outlet"):
                dom = re.sub(r"[^a-z0-9]+", "-", str(ex["outlet"]).lower()).strip("-") + ".outlet"
                break
        if dom:
            it.domain = dom


@dataclass
class Candidate:
    """One or more items whose titles say they are the same article (N3)."""

    items: list[NormItem]
    signal: float = 0.0
    cid: str = ""
    title: str = ""
    snippet: str = ""
    published_at: datetime | None = None
    first_seen: datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def urls(self) -> set[str]:
        return {it.url for it in self.items}

    @property
    def source_ids(self) -> list[str]:
        return sorted({s for it in self.items for s in it.source_ids})


def _dedupe_key(title: str) -> str:
    return title_key(title)


def dedupe_titles(items: Sequence[NormItem], threshold: float) -> list[list[NormItem]]:
    """Union items whose cleaned titles are equal or near-equal (rapidfuzz token_sort_ratio >=
    threshold). Titles with fewer than 4 words merge only on exact equality."""
    keys = [_dedupe_key(it.title) for it in items]
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    first: dict[str, int] = {}
    for i, k in enumerate(keys):
        if not k:
            continue
        if k in first:
            union(first[k], i)
        else:
            first[k] = i
    long_idx = [i for i, k in enumerate(keys) if len(k.split()) >= 4 and first.get(k) == i]
    if len(long_idx) > 1:
        import numpy as np  # rapidfuzz.process.cdist returns a numpy matrix

        texts = [keys[i] for i in long_idx]
        sim = process.cdist(texts, texts, scorer=fuzz.token_sort_ratio, score_cutoff=threshold, workers=1)
        for a, b in np.argwhere(np.triu(sim >= threshold, k=1)):
            union(long_idx[int(a)], long_idx[int(b)])
    groups: dict[int, list[NormItem]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return [groups[r] for r in sorted(groups)]


def _rank_factor(rank: int | None, scale: float) -> float:
    return 1.0 / (1.0 + (rank - 1) / scale) if rank else 0.0


def candidate_signal(c: Candidate, beat: BeatCfg, sources: SourceIndex, *, hn_signal: bool) -> float:
    """Cheap pre-sort signal (higher = stronger): per source weight x position, plus coverage,
    HN points and a primary-source bonus. Only orders candidates for the cap; the editor ranks."""
    weights = {s.id: s.weight for s in beat.headline_sources}
    score = 0.0
    for sid in c.source_ids:
        obs = [o for it in c.items for o in it.observations if o.source_id == sid]
        ranks = [o.rank for o in obs if o.rank is not None]
        best = min(ranks) if ranks else None
        if sources.type(sid) in FRONTPAGE_TYPES:
            pos = 0.4 + 0.6 * _rank_factor(best, 10) if best else 0.6
        else:
            pos = 0.5 + 0.5 * _rank_factor(best, 20) if best else 0.6
        score += weights.get(sid, 0.3) * pos
    domains = {it.domain for it in c.items if not it.unresolved_aggregator}
    score += 0.25 * max(0, len(domains) - 1)
    if hn_signal:
        pts = [
            o.engagement.hn_points
            for it in c.items
            for o in it.observations
            if o.engagement and o.engagement.hn_points is not None
        ]
        if pts:
            score += 0.3 * min(1.0, math.log1p(max(pts)) / math.log1p(500))
    if any(it.domain in sources.primary_domains for it in c.items) or any(
        s in sources.primary_ids for s in c.source_ids
    ):
        score += 0.2
    return round(score, 4)


def _rep_item(items: Sequence[NormItem], sources: SourceIndex) -> NormItem:
    def key(it: NormItem) -> tuple:
        tiers = [t for t in (sources.tier(s) for s in it.source_ids) if t is not None]
        return (is_google_news(it.url), it.unresolved_aggregator, min(tiers) if tiers else 9, it.item_id)

    return min(items, key=key)


def build_candidates(
    items: Sequence[NormItem],
    beat: BeatCfg,
    sources: SourceIndex,
    *,
    threshold: float,
    hn_signal: bool,
) -> list[Candidate]:
    """Dedupe items into candidates and order them by signal; ids c1..cN follow that order."""
    out = []
    for g in dedupe_titles(items, threshold):
        rep = _rep_item(g, sources)
        snippets = sorted({it.snippet for it in g if it.snippet}, key=lambda s: (-len(s), s))
        published = [it.published_at for it in g if it.published_at is not None]
        c = Candidate(
            items=[rep] + [x for x in g if x is not rep],
            title=rep.title,
            snippet=snippets[0] if snippets else "",
            published_at=min(published) if published else None,
            first_seen=min(it.first_seen for it in g),
        )
        c.signal = candidate_signal(c, beat, sources, hn_signal=hn_signal)
        out.append(c)
    out.sort(key=lambda c: (-c.signal, c.first_seen, c.items[0].item_id))
    for n, c in enumerate(out, 1):
        c.cid = f"c{n}"
    return out


# ---- rendering ------------------------------------------------------------------------------------

_TAG = re.compile(r"<\s*/?\s*candidates", re.I)


def _field(s: str, limit: int) -> str:
    s = clean_text(strip_invisible(s)).replace("|", "/")
    s = _TAG.sub("[candidates", s)
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def render_candidate(c: Candidate, sources: SourceIndex, *, hn_signal: bool, snippet_chars: int) -> str:
    date = f"{_utc(c.published_at):%Y-%m-%d %H:%MZ}" if c.published_at else "undated"
    outlets: list[str] = []
    for it in c.items:
        name = item_outlet(it)
        if name not in outlets:
            outlets.append(name)
    for sid in c.source_ids:  # aggregators / front pages that listed it
        if sources.type(sid) in FRONTPAGE_TYPES and sid not in outlets:
            outlets.append(sid)
    out_txt = ", ".join(outlets[:4]) + (f" +{len(outlets) - 4} more" if len(outlets) > 4 else "")
    snippet = c.snippet
    if snippet and (
        fuzz.partial_ratio(title_key(c.title), title_key(snippet)) >= 90 and len(snippet) < len(c.title) + 40
    ):
        snippet = ""  # e.g. Google News descriptions just repeat the title and outlet
    sigs = []
    for sid in c.source_ids:
        if sources.type(sid) not in FRONTPAGE_TYPES:
            continue
        obs = [o for it in c.items for o in it.observations if o.source_id == sid]
        ranks = [o.rank for o in obs if o.rank is not None]
        s = f"{sid} #{min(ranks)}" if ranks else sid
        if hn_signal and sources.is_hn(sid):
            pts = [o.engagement.hn_points for o in obs if o.engagement and o.engagement.hn_points is not None]
            cms = [o.engagement.comments for o in obs if o.engagement and o.engagement.comments is not None]
            if pts:
                s += f" {max(pts)}pts"
            if cms:
                s += f" {max(cms)}c"
        sigs.append(s)
    return " | ".join(
        [
            c.cid,
            date,
            _field(out_txt, 120),
            _field(c.title, 200),
            _field(snippet, snippet_chars) if snippet else "-",
            ", ".join(sigs) or "-",
        ]
    )


# ---- model calls ----------------------------------------------------------------------------------


def _estimate_tokens(messages: Sequence[Message]) -> int:
    return sum(len(m.content) for m in messages) // 4 + 16 * len(messages)


def _record(
    prompt: Prompt,
    resp: ChatResponse | None,
    *,
    ok: bool,
    error: str | None = None,
    output: dict | None = None,
) -> LlmCallRecord:
    return LlmCallRecord(
        step="N5",
        role=prompt.role,
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.sha256,
        model=resp.model if resp else None,
        provider=resp.provider if resp else None,
        tokens_in=resp.usage.tokens_in if resp else 0,
        tokens_out=resp.usage.tokens_out if resp else 0,
        tokens_cached=resp.usage.tokens_cached if resp else 0,
        cost_usd=resp.usage.cost_usd if resp else None,
        cached=resp.cached if resp else False,
        latency_ms=resp.latency_ms if resp else 0,
        ok=ok,
        error=error,
        output=output,
    )


def _raw_output(resp: ChatResponse) -> dict | None:
    raw: Any = resp.parsed
    if raw is None and resp.content:
        m = re.search(r"\{.*\}", resp.content, re.S)
        try:
            raw = json.loads(m.group(0) if m else resp.content)
        except (json.JSONDecodeError, AttributeError):
            return None
    return raw if isinstance(raw, dict) else None


@dataclass
class EditorStory:
    """A story as decided by the editor, with candidate ids resolved."""

    candidates: list[Candidate]
    headline: str
    summary: str
    subtopics: list[str]
    entities: list[str]
    importance: int
    evidence: list[str]
    call: int = 1


def parse_stories(
    raw: Mapping[str, Any] | None, chunk: Sequence[Candidate], call: int
) -> tuple[list[EditorStory], dict[str, int]]:
    """Validate each story; keep only ids from this chunk, each id in at most one story."""
    by_id = {c.cid: c for c in chunk}
    used: set[str] = set()
    out: list[EditorStory] = []
    stats = {"invalid_stories": 0, "unknown_ids": 0, "repeated_ids": 0}
    for s in (raw or {}).get("stories", []) or []:
        if isinstance(s, Mapping) and "importance" in s:
            s = dict(s)
            try:
                s["importance"] = int(round(float(s["importance"])))
            except (TypeError, ValueError):
                pass
        try:
            st = EditorStoryOut.model_validate(s)
        except ValidationError:
            stats["invalid_stories"] += 1
            continue
        ids = []
        for i in st.ids:
            i = str(i).strip()
            if i not in by_id:
                stats["unknown_ids"] += 1
            elif i in used or i in ids:
                stats["repeated_ids"] += 1
            else:
                ids.append(i)
        if not ids:
            stats["invalid_stories"] += 1
            continue
        used.update(ids)
        out.append(
            EditorStory(
                candidates=[by_id[i] for i in ids],
                headline=clean_text(st.headline) or by_id[ids[0]].title,
                summary=clean_text(st.summary),
                subtopics=st.subtopics,
                entities=[clean_text(e) for e in st.entities if clean_text(e)],
                importance=max(0, min(100, st.importance)),
                evidence=[clean_text(e) for e in st.evidence if clean_text(e)],
                call=call,
            )
        )
    return out, stats


def merge_across_calls(stories: Sequence[EditorStory], threshold: float) -> tuple[list[EditorStory], int]:
    """Stories from different calls (disjoint candidates) that are the same event: merge when the
    headlines, or the representative titles, are similar (rapidfuzz token_set_ratio)."""
    merged: list[EditorStory] = []
    n_merges = 0
    for s in sorted(stories, key=lambda s: (-s.importance, s.call)):
        target = None
        for m in merged:
            if m.call == s.call and s.call >= 0:
                continue
            sim = max(
                fuzz.token_set_ratio(m.headline.lower(), s.headline.lower()),
                fuzz.token_sort_ratio(title_key(m.candidates[0].title), title_key(s.candidates[0].title)),
            )
            if sim >= threshold:
                target = m
                break
        if target is None:
            merged.append(s)
            continue
        n_merges += 1
        target.candidates.extend(s.candidates)
        target.entities = list(dict.fromkeys(target.entities + s.entities))
        target.evidence = list(dict.fromkeys(target.evidence + s.evidence))[:4]
        target.subtopics = list(dict.fromkeys(target.subtopics + s.subtopics))[:3]
        target.call = -1  # merged: may absorb stories from any call
    return merged, n_merges


# ---- identity -------------------------------------------------------------------------------------


@dataclass
class Group:
    items: list[NormItem]
    story: EditorStory | None
    title: str
    story_id: str = ""
    first_seen: datetime | None = None
    item_urls: list[str] = field(default_factory=list)
    update_of: str | None = None
    continued_from: str | None = None
    match: dict | None = None


def assign_identity(
    groups: Sequence[Group],
    *,
    beat: str,
    at: datetime,
    history: Sequence[Story],
    lookback_days: int,
    update_gap_hours: float,
    title_threshold: float,
) -> None:
    """Story ids across runs (same rules as classic stories.py, without embeddings): match each
    group to at most one prior story by shared URLs, else by title similarity; continuation keeps
    the prior id, new facts (items first seen >= gap after the prior) make an update."""
    cutoff = at - timedelta(days=lookback_days)
    priors = [s for s in history if s.beat == beat and cutoff <= _utc(s.first_seen) <= at]
    prior_urls = [set(p.item_urls) for p in priors]
    prior_keys = [title_key(p.title) for p in priors]
    used_ids: set[str] = set()
    taken: set[int] = set()
    gap = timedelta(hours=update_gap_hours)

    def unique(sid: str, salt: str) -> str:
        while sid in used_ids:
            sid = f"{sid}-{hashlib.sha1(salt.encode()).hexdigest()[:4]}"
            salt += "+"
        used_ids.add(sid)
        return sid

    def best_match(g: Group) -> tuple[int, str, float] | None:
        urls = {it.url for it in g.items}
        shared = [(len(urls & pu), i) for i, pu in enumerate(prior_urls) if i not in taken and urls & pu]
        if shared:
            n, i = max(shared, key=lambda x: (x[0], -x[1]))
            return i, "shared_url", float(n)
        titles = [title_key(t) for t in {g.title, *(it.title for it in g.items)} if t]
        titles = [t for t in titles if len(t.split()) >= 4]
        best: tuple[int, str, float] | None = None
        for i, pk in enumerate(prior_keys):
            if i in taken or len(pk.split()) < 4:
                continue
            sim = max((fuzz.token_sort_ratio(t, pk) for t in titles), default=0.0)
            if sim >= title_threshold and (best is None or sim > best[2]):
                best = (i, "title", round(sim, 1))
        return best

    for g in groups:
        urls = sorted({it.url for it in g.items})
        earliest = min(g.items, key=lambda it: (it.first_seen, it.url))
        m = best_match(g)
        if m is not None:
            i, method, score = m
            prior = priors[i]
            g.match = {"matched": prior.story_id, "method": method, "score": score}
            p_urls = prior_urls[i]
            new_items = [it for it in g.items if it.url not in p_urls]
            new_facts = [it for it in new_items if it.first_seen >= _utc(prior.first_seen) + gap]
            if new_facts:
                taken.add(i)
                e = min(new_facts, key=lambda it: (it.first_seen, it.url))
                g.story_id = unique(story_id_for(beat, e.url), e.url)
                g.first_seen = min(it.first_seen for it in new_items)
                g.item_urls = urls
                g.update_of = prior.story_id
                continue
            if prior.story_id not in used_ids:
                taken.add(i)
                used_ids.add(prior.story_id)
                g.story_id = prior.story_id
                g.first_seen = min(_utc(prior.first_seen), *(it.first_seen for it in g.items))
                g.item_urls = sorted(p_urls | set(urls))
                g.update_of = prior.update_of
                g.continued_from = prior.story_id
                continue
        g.story_id = unique(story_id_for(beat, earliest.url), earliest.url)
        g.first_seen = min(it.first_seen for it in g.items)
        g.item_urls = urls


# ---- entry point ----------------------------------------------------------------------------------


def editor_version(
    settings: Settings, editor: EditorSettings, prompt_hash: str, flags: ExperimentFlags
) -> str:
    blob = json.dumps(
        {
            "impl": "editor",
            "settings": settings.model_dump(),
            "editor": editor.model_dump(),
            "prompts": {editor.prompt: prompt_hash},
            "hn_signal": flags.hn_signal,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def run_editor_newsroom(
    beat_id: str,
    at: datetime,
    observations: Sequence[HeadlineObservation],
    model_client: ModelClient,
    tools: Mapping[str, Tool] | Sequence[Tool] | None,
    flags: ExperimentFlags | Mapping[str, Any] | None = None,
    *,
    history: Sequence[Story] = (),
    previous_run_at: datetime | None = None,
    run_id: str = "live",
    mode: Mode = "live",
    job_id: str | None = None,
    cache_namespace: str | None = None,
    embedder: Any = None,
    settings: Settings | None = None,
    budget: TokenBudget | None = None,
    editor_settings: EditorSettings | None = None,
) -> tuple[Digest, Trace]:
    """Produce one beat's digest at `at` (same contract as classic `run_newsroom`).

    `embedder` is accepted for signature parity and ignored. `tools` is used only by the optional
    gap-fill search (`newsroom_editor.gap_search`), through the `search` tool, whose live or replay
    backend is chosen by the caller. `editor_settings` defaults to agent.yaml `newsroom_editor`.
    """
    del embedder  # no embeddings
    at = _utc(at)
    fl = flags if isinstance(flags, ExperimentFlags) else ExperimentFlags(**dict(flags or {}))
    st = settings or Settings.from_config()
    es = editor_settings or EditorSettings.from_config()
    beat = loader.beats()[beat_id]
    budget = budget or TokenBudget(input_tokens=st.budget.input_tokens, fetches=0)
    prompt = load_prompt(es.prompt)
    version = editor_version(st, es, prompt.sha256, fl)
    trace = NewsroomTrace(
        run_id=run_id,
        beat=beat_id,
        at=at,
        version=version,
        flags=fl.model_dump(),
        settings=st.model_dump() | {"impl": "editor", "editor": es.model_dump()},
        prompt_hashes={es.prompt: prompt.sha256},
        embedder=EMBEDDER_NAME,
    )
    sources = SourceIndex(beat, st.newsroom.primary_sources.get(beat_id, []))

    # ---- N1b gap-fill search (optional) ------------------------------------------------------------
    if es.gap_search:
        found, info = await gap_search_observations(
            beat_id, beat.name, at, tools, es, mode=mode, run_id=run_id, job_id=job_id,
            window_hours=st.newsroom.window_hours,
        )
        observations = [*observations, *found]
        trace.steps.append(TraceStep(step="N1b", name="gap_search", detail=info))

    # ---- N2 normalise ----------------------------------------------------------------------------
    window_start = at - timedelta(hours=st.newsroom.window_hours)
    in_window: list[HeadlineObservation] = []
    dropped = {"other_beat": 0, "out_of_window": 0, "future": 0, "hn_ablation": 0}
    for o in observations:
        if o.beat != beat_id:
            dropped["other_beat"] += 1
        elif _utc(o.observed_at) > at:
            dropped["future"] += 1  # never use anything observed after the run time
        elif _utc(o.observed_at) <= window_start:
            dropped["out_of_window"] += 1
        elif not fl.hn_signal and sources.is_hn(o.source_id):
            dropped["hn_ablation"] += 1
        else:
            in_window.append(o.model_copy(update={"observed_at": _utc(o.observed_at)}))
    kept, fresh_dropped = freshness_filter(
        in_window,
        window_start=window_start,
        source_type=sources.type,
        max_undated_per_source=es.max_undated_per_source,
    )
    dropped |= fresh_dropped
    stale_by_source: dict[str, int] = {}
    kept_ids = {id(o) for o in kept}
    for o in in_window:
        if id(o) not in kept_ids:
            stale_by_source[o.source_id] = stale_by_source.get(o.source_id, 0) + 1
    items = normalize(kept, {s.id: s.tier for s in beat.headline_sources})
    fix_redirect_domains(items)
    trace.steps.append(
        TraceStep(
            step="N2",
            name="normalise",
            detail={
                "observations_in": len(observations),
                "observations_kept": len(kept),
                "dropped": dropped,
                "freshness_dropped_by_source": dict(sorted(stale_by_source.items())),
                "items": len(items),
                "google_news_items": sum(is_google_news(it.url) for it in items),
                "unresolved_aggregator_links": sum(it.unresolved_aggregator for it in items),
            },
        )
    )

    # ---- N3 dedupe -------------------------------------------------------------------------------
    cands = build_candidates(
        items, beat, sources, threshold=es.title_dedupe_threshold, hn_signal=fl.hn_signal
    )
    trace.steps.append(
        TraceStep(
            step="N3",
            name="title_dedupe",
            detail={
                "items": len(items),
                "candidates": len(cands),
                "multi_item_candidates": sum(len(c.items) > 1 for c in cands),
                "threshold": es.title_dedupe_threshold,
            },
        )
    )

    # ---- N4 candidates ---------------------------------------------------------------------------
    shown = cands[: es.max_candidates]
    size = max(1, es.chunk_size)
    chunks = [shown[i : i + size] for i in range(0, len(shown), size)][: max(1, es.max_calls)]
    shown = [c for ch in chunks for c in ch]
    trace.steps.append(
        TraceStep(
            step="N4",
            name="candidates",
            detail={
                "shown": len(shown),
                "cut": len(cands) - len(shown),
                "calls": len(chunks),
                "signal_min_shown": shown[-1].signal if shown else None,
                "signal_top": [{"id": c.cid, "signal": c.signal, "title": c.title} for c in shown[:10]],
            },
        )
    )

    # ---- N5 editor -------------------------------------------------------------------------------
    subtopic_lines = (
        "\n".join(f"- {s.id}: {s.description}" for s in loader.taxonomy().subtopics if s.id in beat.subtopics)
        + "\n- other: none of the above"
    )
    system_text = prompt.render(
        beat_name=beat.name,
        beat_scope=beat.scope.strip(),
        beat_boundaries="\n".join(f"- {b}" for b in beat.boundaries) or "- (none)",
        beat_out_of_scope="\n".join(f"- {b}" for b in beat.out_of_scope) or "- (none)",
        subtopics=subtopic_lines,
        max_stories=es.max_stories,
        min_stories=min(es.min_stories, es.max_stories),
    )
    trace_ctx = TraceCtx(run_id=run_id, user_id=None, job_id=job_id, role=EDITOR_ROLE, step="N5")

    async def call(n: int, chunk: list[Candidate]) -> tuple[list[EditorStory], dict]:
        lines = "\n".join(
            render_candidate(c, sources, hn_signal=fl.hn_signal, snippet_chars=es.snippet_chars)
            for c in chunk
        )
        header = (
            f"Run time: {at.isoformat()}. Beat: {beat_id}. Part {n} of {len(chunks)}. "
            f"{len(chunk)} candidates.\n\n"
        )
        msgs = [
            Message(role="system", content=system_text, cache_breakpoint=True),
            Message(role="user", content=f"{header}<candidates>\n{lines}\n</candidates>"),
        ]
        info: dict[str, Any] = {"call": n, "candidates": len(chunk), "ok": False}
        if budget.used_input_tokens + _estimate_tokens(msgs) > budget.input_tokens:
            trace.degraded.append(f"N5 call {n}: skipped (token budget)")
            info["error"] = "token budget"
            return [], info
        req = ChatRequest(
            role=EDITOR_ROLE,
            messages=msgs,
            response_schema=EditorOut,
            max_output_tokens=es.max_output_tokens,
            temperature=0.0,
            budget=budget,
            trace=trace_ctx,
            cache_namespace=cache_namespace or run_id,
        )
        try:
            resp = await model_client.chat(req)
        except BudgetExceeded as e:
            trace.add_llm(_record(prompt, None, ok=False, error=f"budget: {e}"))
            trace.degraded.append(f"N5 call {n}: budget exceeded")
            info["error"] = "budget"
            return [], info
        except StructuredOutputInvalid as e:
            trace.add_llm(_record(prompt, None, ok=False, error=str(e)[:300]))
            trace.degraded.append(f"N5 call {n}: invalid structured output")
            info["error"] = "invalid structured output"
            return [], info
        raw = _raw_output(resp)
        stories, stats = parse_stories(raw, chunk, n)
        err = None if raw is not None else "output is not JSON"
        trace.add_llm(_record(prompt, resp, ok=err is None, error=err, output=raw))
        info |= {"ok": err is None, "stories": len(stories), **stats}
        if err:
            trace.degraded.append(f"N5 call {n}: {err}")
        return stories, info

    results = await asyncio.gather(*(call(n, ch) for n, ch in enumerate(chunks, 1)))
    ed_stories = [s for r, _ in results for s in r]
    n_merges = 0
    if len(chunks) > 1:
        ed_stories, n_merges = merge_across_calls(ed_stories, es.merge_title_threshold)
    rules_only = bool(shown) and not any(info.get("ok") for _, info in results)
    if rules_only:
        trace.degraded.append("N5: no editor output; digest is rules-only")
    trace.steps.append(
        TraceStep(
            step="N5",
            name="editor",
            detail={
                "calls": [info for _, info in results],
                "stories": len(ed_stories),
                "cross_call_merges": n_merges,
                "grouped_candidates": sum(len(s.candidates) for s in ed_stories),
                "rules_only": rules_only,
                "input_tokens_used": budget.used_input_tokens,
            },
        )
    )

    # ---- N6 identity + digest --------------------------------------------------------------------
    in_story = {c.cid for s in ed_stories for c in s.candidates}
    groups: list[Group] = []
    for s in sorted(ed_stories, key=lambda s: (-s.importance, s.candidates[0].cid)):
        its = [it for c in s.candidates for it in c.items]
        groups.append(Group(items=its, story=s, title=s.headline))
    for c in cands:
        if c.cid not in in_story:
            groups.append(Group(items=list(c.items), story=None, title=c.title))
    assign_identity(
        groups,
        beat=beat_id,
        at=at,
        history=history,
        lookback_days=st.clustering.identity_lookback_days,
        update_gap_hours=st.newsroom.update_gap_hours,
        title_threshold=es.identity_title_threshold,
    )
    last_run = _utc(previous_run_at) if previous_run_at else window_start
    feats: dict[str, StoryFeatures] = {}
    rules: dict[str, float] = {}
    for g in groups:
        feats[g.story_id] = compute_features(
            g.items, sources, at=at, last_run_at=last_run, hn_signal=fl.hn_signal
        )
        rules[g.story_id] = importance_rules(
            feats[g.story_id], st.newsroom.rules_weights, hn_signal=fl.hn_signal
        )
    trace.stories = sorted(
        (
            Story(
                story_id=g.story_id,
                beat=beat_id,
                title=g.title,
                first_seen=g.first_seen or at,
                item_urls=g.item_urls,
                features=feats[g.story_id],
                update_of=g.update_of,
            )
            for g in groups
        ),
        key=lambda s: s.story_id,
    )

    paywalled = {d.lower() for d in es.paywalled_domains}

    def url_order(its: Sequence[NormItem]) -> list[str]:
        def key(it: NormItem) -> tuple:
            primary = it.domain in sources.primary_domains or any(
                s in sources.primary_ids for s in it.source_ids
            )
            tier = min((sources.tier(s) or 9) for s in it.source_ids) if it.source_ids else 9
            # Publisher URLs before Google News redirects / unresolved aggregator pages, even a primary
            # source's redirect: a direct URL fetches in one request, a redirect needs resolving first.
            # Redirects are kept (the fetch tool resolves them lazily, app/sources/google_news.py):
            # resolving all ~600 per run here would cost two Google requests each, mostly for stories
            # nobody reads, and N2-N6 stay free of network I/O.
            return (
                is_google_news(it.url) or it.unresolved_aggregator,
                not primary,
                it.domain in paywalled,
                tier,
                it.first_seen,
                it.url,
            )

        return list(dict.fromkeys(it.url for it in sorted(its, key=key)))

    allowed_subs = set(beat.subtopics) | {"other"}
    scored: list[tuple[Group, DigestEntryLLM]] = []
    for g in groups:
        if g.story is not None:
            s = g.story
            notes = []
            ev = s.evidence[:4]
            if not ev:
                ev = rules_evidence(feats[g.story_id], sorted({x for it in g.items for x in it.source_ids}))
                notes.append("no evidence given; rules evidence shown")
            entry = DigestEntryLLM(
                story_id=g.story_id,
                headline=s.headline,
                summary=s.summary or s.candidates[0].snippet[:300] or s.headline,
                subtopics=[x for x in s.subtopics if x in allowed_subs][:3] or ["other"],
                entities=s.entities[:10],
                importance=s.importance,
                evidence=ev,
                is_update_of=g.update_of,  # code-owned (story identity)
                notes_for_editor="; ".join(notes) or None,
            )
            scored.append((g, entry))
        elif rules_only:
            r = rules[g.story_id]
            snippet = next((it.snippet for it in g.items if it.snippet), "")
            scored.append(
                (
                    g,
                    DigestEntryLLM(
                        story_id=g.story_id,
                        headline=g.title,
                        summary=(snippet.split(". ")[0].rstrip(".") + ".") if snippet else g.title,
                        subtopics=["other"],
                        entities=[],
                        importance=max(0, min(100, round(r))),
                        evidence=rules_evidence(
                            feats[g.story_id], sorted({x for it in g.items for x in it.source_ids})
                        ),
                        is_update_of=g.update_of,
                        notes_for_editor="rules-only fallback: no editor output",
                    ),
                )
            )
    above = [(g, e) for g, e in scored if e.importance >= st.newsroom.score_floor]
    ranked = sorted(above, key=lambda ge: (-ge[1].importance, -rules[ge[1].story_id], ge[1].story_id))
    ranked = ranked[: beat.digest_size]
    entries = [
        DigestEntry(
            **e.model_dump(),
            importance_rules=rules[e.story_id],
            features=feats[e.story_id],
            urls=url_order(g.items),
        )
        for g, e in ranked
    ]
    digest_id = (
        f"{beat_id}-{at:%Y%m%dT%H%MZ}-"
        + hashlib.sha256(f"{run_id}|{beat_id}|{at.isoformat()}|{version}".encode()).hexdigest()[:8]
    )
    digest = Digest(digest_id=digest_id, run_id=run_id, beat=beat_id, at=at, version=version, entries=entries)
    trace.steps.append(
        TraceStep(
            step="N6",
            name="digest",
            detail={
                "digest_id": digest_id,
                "stories": len(groups),
                "entries": len(entries),
                "below_floor": len(scored) - len(above),
                "floor": st.newsroom.score_floor,
                "digest_size": beat.digest_size,
                "prior_matches": [{"story_id": g.story_id, **g.match} for g in groups if g.match is not None],
                "continued": sum(g.continued_from is not None for g in groups),
                "updates": sum(g.update_of is not None and g.continued_from is None for g in groups),
            },
        )
    )
    return digest, trace
