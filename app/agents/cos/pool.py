"""C3 candidate pool (code). TDD §8.2.

Union of digest entries (top N per followed beat, fewer for other beats), personal and adjacent search
finds, carry-over saved items, and updates to stories the user saw. Dedupe by story; suppress repeats.

Clustering finds to stories uses URL identity and title-token Jaccard (the newsroom's N3 embedding
method is not available to this workstream; swap in `same_story` when it is).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.cos.text import canonical_url, jaccard, short_hash, tokens
from app.contracts.agent_io import ShownStory
from app.contracts.brief import Candidate, CandidateOrigin
from app.contracts.digest import Digest, DigestEntry
from app.contracts.sources import SearchHit

SAME_STORY_JACCARD = 0.5
REPEAT_JACCARD = 0.6
NEW_FACTS_MAX_JACCARD = 0.8


@dataclass
class Find:
    """One search hit from C2, with the plan query kind that found it."""

    hit: SearchHit
    kind: str  # "personal" | "adjacent"
    query: str


@dataclass
class PoolItem:
    candidate: Candidate
    beat: str | None = None
    digest_importance: int | None = None
    importance_evidence: list[str] = field(default_factory=list)
    subtopics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    update_of: str | None = None
    snippets: dict[str, SearchHit] = field(default_factory=dict)  # canonical url -> hit (snippet passages)
    queries: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return self.candidate.story_ref

    @property
    def origin(self) -> CandidateOrigin:
        return self.candidate.origin

    def title_tokens(self) -> set[str]:
        return tokens(self.candidate.headline)


def has_new_facts(entry: DigestEntry, shown: ShownStory) -> bool:
    """Proxy for `update_requires_new_facts`: headline + summary must differ from what was shown."""
    return (
        jaccard(tokens(entry.headline + " " + entry.summary), tokens(shown.headline)) < NEW_FACTS_MAX_JACCARD
    )


def _digest_item(e: DigestEntry, beat: str, origin: CandidateOrigin) -> PoolItem:
    return PoolItem(
        candidate=Candidate(
            story_ref=e.story_id, origin=origin, headline=e.headline, summary=e.summary, urls=list(e.urls)
        ),
        beat=beat,
        digest_importance=e.importance,
        importance_evidence=list(e.evidence),
        subtopics=list(e.subtopics),
        entities=list(e.entities),
        update_of=e.is_update_of,
    )


def build_pool(
    digests: list[Digest],
    finds: list[Find],
    history: list[ShownStory],
    *,
    followed_beats: list[str],
    update_requires_new_facts: bool = True,
    top_followed: int = 30,
    top_other: int = 10,
    pool_max: int = 80,
) -> tuple[list[PoolItem], list[str]]:
    notes: list[str] = []
    shown = {h.story_ref: h for h in history}
    shown_urls = {canonical_url(u) for h in history for u in h.urls}
    shown_titles = [tokens(h.headline) for h in history]
    followed = set(followed_beats)
    items: dict[str, PoolItem] = {}

    # digest entries
    for d in sorted(digests, key=lambda d: d.beat):
        limit = top_followed if (not followed or d.beat in followed) else top_other
        for e in sorted(d.entries, key=lambda e: (-e.importance, e.story_id))[:limit]:
            if e.story_id in items:
                continue
            if e.story_id in shown:
                notes.append(f"repeat dropped: {e.story_id}")
                continue
            origin: CandidateOrigin = "digest"
            if e.is_update_of and e.is_update_of in shown:
                prev = shown[e.is_update_of]
                if update_requires_new_facts and not has_new_facts(e, prev):
                    notes.append(f"update without new facts dropped: {e.story_id}")
                    continue
                origin = "update" if prev.engaged or prev.saved_for_later else "digest"
            items[e.story_id] = _digest_item(e, d.beat, origin)

    url_index = {canonical_url(u): ref for ref, it in items.items() for u in it.candidate.urls}

    # search finds (personal before adjacent so a story found by both counts as personal)
    ordered = sorted(enumerate(finds), key=lambda t: (t[1].kind != "personal", t[0]))
    for _, f in ordered:
        cu = canonical_url(f.hit.url)
        tt = tokens(f.hit.title)
        if cu in shown_urls or any(jaccard(tt, s) >= REPEAT_JACCARD for s in shown_titles):
            notes.append(f"repeat find dropped: {f.hit.url}")
            continue
        ref = url_index.get(cu)
        if ref is None:
            best = max(((jaccard(tt, it.title_tokens()), r) for r, it in items.items()), default=(0.0, ""))
            if best[0] >= SAME_STORY_JACCARD:
                ref = best[1]
        if ref is not None:
            it = items[ref]
            if f.hit.url not in it.candidate.urls:
                it.candidate.urls.append(f.hit.url)
            it.snippets.setdefault(cu, f.hit)
            if f.query not in it.queries:
                it.queries.append(f.query)
            url_index[cu] = ref
            continue
        ref = "u:" + short_hash(cu)
        origin = "adjacent" if f.kind == "adjacent" else "personal"
        items[ref] = PoolItem(
            candidate=Candidate(
                story_ref=ref, origin=origin, headline=f.hit.title, summary=f.hit.snippet, urls=[f.hit.url]
            ),
            snippets={cu: f.hit},
            queries=[f.query],
        )
        url_index[cu] = ref

    # carry-over saved items
    for h in sorted(history, key=lambda h: (h.shown_on, h.story_ref)):
        if not h.saved_for_later:
            continue
        items[h.story_ref] = PoolItem(
            candidate=Candidate(
                story_ref=h.story_ref, origin="carryover", headline=h.headline, urls=list(h.urls)
            ),
            subtopics=list(h.subtopics),
            entities=list(h.entities),
            update_of=h.update_of,
        )

    pool = list(items.values())
    if len(pool) > pool_max:
        keep_first = [p for p in pool if p.origin != "digest"]
        digest_items = sorted(
            (p for p in pool if p.origin == "digest"), key=lambda p: (-(p.digest_importance or 0), p.ref)
        )
        pool = (keep_first + digest_items)[:pool_max]
        notes.append(f"pool truncated to {pool_max}")
    return pool, notes
