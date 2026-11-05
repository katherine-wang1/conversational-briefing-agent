"""INTERIM live headline adapters for the integration smoke test. DELETE when W-A's parsers merge.

W-A (`ws/archive`) owns the real source parsers (`app/sources/*`). Until that lands, this tiny shim lets
the live newsroom run against a few real sources: RSS/Atom feeds (types `rss`, `reddit_top`), the HN
front page (Firebase API, true front-page rank), and Techmeme's RSS feed (type `techmeme`; the article
URL is taken from the item body). `frontpage`, `arxiv` and `hf_papers` have no interim adapter.

Feed entries published more than 3 days before `at` are dropped. `app.tools.headlines` only falls back
to these when `app.sources` provides no adapter for a type, so W-A's adapters win automatically.
Honest User-Agent, one GET per feed, short timeouts.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import UTC, datetime, timedelta

import httpx

from app.config.loader import HeadlineSourceCfg
from app.contracts.sources import Engagement, HeadlineObservation
from app.tools.fetch import USER_AGENT

TIMEOUT_S = 15.0
MAX_ITEMS = 30
MAX_AGE = timedelta(days=3)  # feeds list old posts too; older entries are not "visible news" at `at`
TECHMEME_FEED = "https://www.techmeme.com/feed.xml"
HN_API = "https://hacker-news.firebaseio.com/v0"
_HREF = re.compile(r'href="(https?://[^"]+)"', re.I)
_TAG = re.compile(r"<[^>]+>")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _dt(struct) -> datetime | None:  # noqa: ANN001 - feedparser time.struct_time
    return datetime(*struct[:6], tzinfo=UTC) if struct else None


def _snippet(html: str | None) -> str | None:
    if not html:
        return None
    text = re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()
    return text[:500] or None


async def rss(cfg: HeadlineSourceCfg, beat: str, at: datetime) -> list[HeadlineObservation]:
    import feedparser

    url = cfg.url or ""
    if cfg.type == "techmeme" and not url.endswith(".xml"):
        url = TECHMEME_FEED  # the configured URL is the HTML front page; W-A parses that
    async with _client() as c:
        r = await c.get(url)
        r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for i, e in enumerate(feed.entries[:MAX_ITEMS]):
        published = _dt(e.get("published_parsed") or e.get("updated_parsed"))
        if published and published < at - MAX_AGE:
            continue
        url, title = e.get("link"), (e.get("title") or "").strip()
        summary = e.get("summary")
        if cfg.type == "techmeme":  # item link is the Techmeme permalink; the body links the article
            hrefs = [html.unescape(u) for u in _HREF.findall(summary or "")]
            url = next((u for u in hrefs if "techmeme.com" not in u), url)
        if not url or not title:
            continue
        out.append(
            HeadlineObservation(
                source_id=cfg.id,
                beat=beat,
                observed_at=at,
                url=url,
                title=title,
                rank=i + 1,
                snippet=None if cfg.type == "techmeme" else _snippet(summary),
                published_at=published,
            )
        )
    return out


async def hn_front(cfg: HeadlineSourceCfg, beat: str, at: datetime) -> list[HeadlineObservation]:
    async with _client() as c:
        r = await c.get(f"{HN_API}/topstories.json")
        r.raise_for_status()
        ids = r.json()[:MAX_ITEMS]
        items = await asyncio.gather(*(c.get(f"{HN_API}/item/{i}.json") for i in ids), return_exceptions=True)
    kw = (cfg.params.get("keyword_filter") or "").lower()
    out = []
    for rank, (iid, resp) in enumerate(zip(ids, items, strict=True), 1):
        if isinstance(resp, BaseException) or resp.status_code != 200 or not resp.json():
            continue
        it = resp.json()
        title = it.get("title") or ""
        if kw and kw not in title.lower():
            continue
        out.append(
            HeadlineObservation(
                source_id=cfg.id,
                beat=beat,
                observed_at=at,
                url=it.get("url") or f"https://news.ycombinator.com/item?id={iid}",
                title=title,
                rank=rank,
                engagement=Engagement(hn_points=it.get("score"), comments=it.get("descendants")),
                published_at=datetime.fromtimestamp(it["time"], UTC) if it.get("time") else None,
                external_id=str(iid),
            )
        )
    return out


ADAPTERS = {"rss": rss, "reddit_top": rss, "techmeme": rss, "hn_front": hn_front}
