"""N3 for the newsroom: cluster items into stories and assign stable story ids.

Identity rules (story identity across runs and days):
- A cluster is matched to at most one earlier story from the last `identity_lookback_days`
  (`clustering.match_to_prior`: shared URL, else embedding similarity, else tiebreak).
- **Continuation:** matched, and no new facts -> the cluster keeps the earlier `story_id`
  (and its `update_of`); item URLs and `first_seen` are carried over.
- **Update:** matched, and the cluster has *new facts* = items not in the earlier story first seen
  at least `update_gap_hours` after the earlier story's `first_seen` -> a new story whose
  `update_of` is the earlier story id.
- **New:** no match.

Story ids are deterministic: `<beat>-<sha1(beat|canonical url of the earliest item)[:12]>`, where
"earliest" is the earliest *new-fact* item for updates. Same inputs give the same ids.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.agents.newsroom.clustering import (
    ClusterResult,
    Embedder,
    PriorMatch,
    PriorRef,
    Tiebreaker,
    cluster_texts,
    match_to_prior,
)
from app.agents.newsroom.features import SourceIndex
from app.agents.newsroom.normalize import NormItem
from app.contracts.digest import Story


def story_id_for(beat: str, url: str) -> str:
    return f"{beat}-{hashlib.sha1(f'{beat}|{url}'.encode()).hexdigest()[:12]}"


@dataclass
class StoryDraft:
    story_id: str
    items: list[NormItem]
    title: str
    first_seen: datetime
    item_urls: list[str]  # all URLs that belong to the story (incl. carried over)
    update_of: str | None = None
    continued_from: str | None = None
    match: PriorMatch | None = None
    extra: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.items[0].text if self.items else self.title


def _rep_item(items: Sequence[NormItem], sources: SourceIndex) -> NormItem:
    def key(it: NormItem) -> tuple:
        tiers = [t for t in (sources.tier(s) for s in it.source_ids) if t is not None]
        return (
            min(tiers) if tiers else 9,
            it.unresolved_aggregator,
            -len(it.observations),
            it.first_seen,
            it.item_id,
        )

    return min(items, key=key)


@dataclass
class StoriesResult:
    drafts: list[StoryDraft]
    clustering: ClusterResult
    matches: list[PriorMatch]


async def build_stories(
    items: Sequence[NormItem],
    *,
    beat: str,
    at: datetime,
    sources: SourceIndex,
    embedder: Embedder,
    tau: float,
    band: float,
    lookback_days: int,
    update_gap_hours: float,
    history: Sequence[Story] = (),
    tiebreaker: Tiebreaker | None = None,
    max_tiebreak_pairs: int = 40,
) -> StoriesResult:
    by_id = {it.item_id: it for it in items}
    cr = await cluster_texts(
        [it.item_id for it in items],
        [it.text for it in items],
        embedder=embedder,
        tau=tau,
        band=band,
        tiebreaker=tiebreaker,
        max_tiebreak_pairs=max_tiebreak_pairs,
    )
    groups: list[list[NormItem]] = []
    for members in cr.clusters:
        g = [by_id[m] for m in members]
        rep = _rep_item(g, sources)
        groups.append([rep] + [x for x in g if x is not rep])

    cutoff = at - timedelta(days=lookback_days)
    priors = [s for s in history if s.beat == beat and s.first_seen >= cutoff and s.first_seen <= at]
    prior_by_id = {s.story_id: s for s in priors}
    matches = await match_to_prior(
        [[it.item_id for it in g] for g in groups],
        [g[0].text for g in groups],
        [{it.url for it in g} for g in groups],
        [[cr.vectors[it.item_id] for it in g] for g in groups],
        [PriorRef(s.story_id, s.title, frozenset(s.item_urls)) for s in priors],
        embedder=embedder,
        tau=tau,
        band=band,
        tiebreaker=tiebreaker,
        max_tiebreak_pairs=max_tiebreak_pairs,
    )
    match_of = {m.cluster_index: m for m in matches}

    drafts: list[StoryDraft | None] = [None] * len(groups)
    used_ids: set[str] = set()
    continued: set[str] = set()
    gap = timedelta(hours=update_gap_hours)

    def unique(sid: str, salt: str) -> str:
        while sid in used_ids:
            sid = f"{sid}-{hashlib.sha1(salt.encode()).hexdigest()[:4]}"
            salt += "+"
        used_ids.add(sid)
        return sid

    def earliest(its: Sequence[NormItem]) -> NormItem:
        return min(its, key=lambda it: (it.first_seen, it.url))

    # matched clusters first, strongest matches first, so continuation is deterministic
    order = sorted(
        range(len(groups)),
        key=lambda ci: (
            ci not in match_of,
            -(match_of[ci].shared_urls if ci in match_of else 0),
            -(match_of[ci].similarity if ci in match_of else 0.0),
            groups[ci][0].item_id,
        ),
    )
    for ci in order:
        g = groups[ci]
        m = match_of.get(ci)
        title = g[0].title
        urls = sorted({it.url for it in g})
        if m is not None:
            prior = prior_by_id[m.ref_id]
            prior_urls = set(prior.item_urls)
            new_items = [it for it in g if it.url not in prior_urls]
            new_facts = [it for it in new_items if it.first_seen >= prior.first_seen + gap]
            if new_facts:
                e = earliest(new_facts)
                sid = unique(story_id_for(beat, e.url), e.url)
                drafts[ci] = StoryDraft(
                    sid,
                    g,
                    title,
                    min(it.first_seen for it in new_items),
                    urls,
                    update_of=prior.story_id,
                    match=m,
                )
                continue
            if prior.story_id not in continued and prior.story_id not in used_ids:
                continued.add(prior.story_id)
                used_ids.add(prior.story_id)
                drafts[ci] = StoryDraft(
                    prior.story_id,
                    g,
                    title,
                    min(prior.first_seen, *(it.first_seen for it in g)),
                    sorted(prior_urls | set(urls)),
                    update_of=prior.update_of,
                    continued_from=prior.story_id,
                    match=m,
                )
                continue
        e = earliest(g)
        sid = unique(story_id_for(beat, e.url), e.url)
        drafts[ci] = StoryDraft(sid, g, title, min(it.first_seen for it in g), urls, match=m)

    out = [d for d in drafts if d is not None]
    return StoriesResult(drafts=out, clustering=cr, matches=matches)
