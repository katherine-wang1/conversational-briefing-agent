"""Live source adapters (TDD §6.3): `observe(cfg, beat, at)` -> HeadlineObservations, using the same parsers
as the archive collectors. `fetch` is injectable (url -> bytes) so callers can add caching / rate limits;
the default is a plain httpx GET with the project's honest User-Agent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from app.config.loader import HeadlineSourceCfg
from app.contracts.sources import HeadlineObservation
from app.sources import arxiv, hf_papers, hn, rss, techmeme
from app.sources.frontpage import parse_frontpage
from app.sources.util import USER_AGENT

Fetch = Callable[[str], bytes]


def http_fetch(url: str, timeout: float = 30.0) -> bytes:
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _keyword_filter(obs: list[HeadlineObservation], kw: str | None) -> list[HeadlineObservation]:
    if not kw:
        return obs
    k = kw.lower()
    return [o for o in obs if k in o.title.lower() or k in (o.snippet or "").lower()]


def observe(
    cfg: HeadlineSourceCfg, beat: str, at: datetime | None = None, fetch: Fetch = http_fetch
) -> list[HeadlineObservation]:
    """Fetch and parse one configured headline source now. `at` defaults to the current time."""
    at = at or datetime.now(UTC)
    kw = cfg.params.get("keyword_filter")
    common = {"source_id": cfg.id, "beat": beat}
    if cfg.type == "techmeme":
        obs = techmeme.parse_techmeme_front(
            fetch(cfg.url or techmeme.TM_BASE), observed_at=at, provenance="live", **common
        )
    elif cfg.type == "hn_front":
        obs = hn.parse_hn_front_html(
            fetch(cfg.url or hn.HN_BASE), observed_at=at, provenance="live", **common
        )
    elif cfg.type == "hf_papers":
        day = at.date().isoformat()
        obs = hf_papers.parse_daily_papers(
            fetch(f"https://huggingface.co/api/daily_papers?date={day}"),
            listing_day=day,
            fetched_at=at,
            **common,
        )
        obs = [o.model_copy(update={"observed_at": at, "provenance": "live"}) for o in obs]
    elif cfg.type == "arxiv":
        cat = cfg.params["cat"]
        entries, _ = arxiv.parse_arxiv_feed(
            fetch(arxiv.query_url(cat, at - timedelta(days=3), at, max_results=500))
        )
        obs = [arxiv.entry_to_observation(e, **common) for e in entries]
        obs = [o for o in obs if o.observed_at <= at]
    elif cfg.type in ("rss", "reddit_top"):
        obs = rss.parse_feed(
            fetch(cfg.url or ""), observed_at=at, provenance="live", base_url=cfg.url, **common
        )
    elif cfg.type == "frontpage":
        obs = parse_frontpage(
            fetch(cfg.url or ""), page_url=cfg.url or "", observed_at=at, provenance="live", **common
        )
    else:  # pragma: no cover - the config loader restricts types
        raise ValueError(f"unknown source type {cfg.type}")
    return _keyword_filter(obs, kw)
