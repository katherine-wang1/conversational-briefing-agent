"""Event Registry / newsapi.ai: events (clusters of articles about one real-world event), ranked by size.

Two uses (datasheet and per-beat example params: docs/sources/eventregistry.md):
* headline source type `eventregistry` (`observe_events`, wired into `app/sources/adapters.observe`): the
  biggest events in the window as HeadlineObservations, rank = position by article count;
* `eventregistry_ranked(beat_cfg, at, params)`: the provider's own ranking as RankedStory, for the
  newsroom comparison.

Request: `POST https://eventregistry.org/api/v1/event/getEvents`, JSON body with `apiKey` (field names as
in the official SDK's QueryEvents / RequestEventsInfo payloads). Dates are day-granular (`dateStart` /
`dateEnd`, YYYY-MM-DD, UTC), so "the 24 h before `at`" becomes the two calendar days it touches.
`totalArticleCount` is the count *at fetch time*: for a past `at` it includes later articles, so such
observations are marked `provenance="hindsight_listing"` and the count stays in `extra` (never in
`engagement`).

`params` (all optional; from `cfg.params` or the `params` argument):
  keywords: str | list[str]      keyword_oper: "or" | "and" (default "or")
  keyword_loc: "body" | "title" | "body,title" (default "body,title")
  concepts: list[str] (concept URIs)   concept_oper: "or" | "and" (default "or")
  categories: list[str] (category URIs, OR)   ignore_keywords: list[str]
  lang: "eng"   count: 1-50 (default 30)   min_articles: int
  top_articles: int (default 0; >0 = one extra getEvent call per event, for the top N articles)
  top_articles_events: int (default 5; how many of the biggest events get that call)
  keyword_filter: str (generic post-filter applied by `observe`)

Key: EVENT_REGISTRY_API_KEY. Without it every call raises ToolError("unavailable"); nothing is invented.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx

from app.config import loader
from app.config.loader import BeatCfg, HeadlineSourceCfg
from app.contracts.sources import HeadlineObservation
from app.sources.ranked import RankedStories, RankedStory
from app.sources.util import USER_AGENT, one_line, parse_iso
from app.tools.base import ToolError

ER_BASE = "https://eventregistry.org"
GET_EVENTS = ER_BASE + "/api/v1/event/getEvents"
GET_EVENT = ER_BASE + "/api/v1/event/getEvent"
RECENT_DAYS = 30  # searches inside the last 30 days are "recent" (cheaper; the only data on the free plan)

Post = Callable[[str, dict[str, Any]], httpx.Response]


def http_post(url: str, body: dict[str, Any], timeout: float = 60.0) -> httpx.Response:
    return httpx.post(url, json=body, headers={"User-Agent": USER_AGENT}, timeout=timeout)


def api_key_from_env() -> str:
    key = os.environ.get("EVENT_REGISTRY_API_KEY", "")
    if key:
        return key
    from app.config.settings import get_settings

    return get_settings().event_registry_api_key


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _as_list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    return [v] if isinstance(v, str) else [str(x) for x in v]


def build_events_body(
    params: dict[str, Any], at: datetime, api_key: str, *, now: datetime | None = None
) -> dict[str, Any]:
    end = _utc(at)
    start = end - timedelta(hours=24)
    body: dict[str, Any] = {
        "apiKey": api_key,
        "resultType": "events",
        "lang": params.get("lang", "eng"),
        "dateStart": start.date().isoformat(),
        "dateEnd": end.date().isoformat(),
        "eventsSortBy": "size",
        "eventsSortByAsc": False,
        "eventsCount": max(1, min(50, int(params.get("count", 30)))),
        "eventsPage": 1,
        "includeEventInfoArticle": True,  # the article the title/summary came from, per language
    }
    kw = _as_list(params.get("keywords"))
    if kw:
        body.update(keyword=kw, keywordOper=params.get("keyword_oper", "or"))
        body["keywordLoc"] = params.get("keyword_loc", "body,title")
    concepts = _as_list(params.get("concepts"))
    if concepts:
        body.update(conceptUri=concepts, conceptOper=params.get("concept_oper", "or"))
    cats = _as_list(params.get("categories"))
    if cats:
        body.update(categoryUri=cats, categoryOper="or")
    ignore = _as_list(params.get("ignore_keywords"))
    if ignore:
        body["ignoreKeyword"] = ignore
    if params.get("min_articles"):
        body["minArticlesInEvent"] = int(params["min_articles"])
    if (_utc(now or datetime.now(UTC)) - start).days < RECENT_DAYS:
        body["forceMaxDataTimeWindow"] = 31  # as the SDK does: stay off the (costlier) archive
    return body


def _check(r: httpx.Response) -> dict[str, Any]:
    if r.status_code in (401, 403):
        raise ToolError(
            "unavailable",
            f"Event Registry refused the request (HTTP {r.status_code}: bad key or no tokens left)",
        )
    if r.status_code == 429:
        raise ToolError("unavailable", "Event Registry: too many simultaneous requests (HTTP 429)")
    if r.status_code >= 400:
        raise ToolError("unavailable", f"Event Registry returned HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as e:
        raise ToolError("unavailable", "Event Registry returned invalid JSON") from e
    if isinstance(data, dict) and data.get("error"):
        raise ToolError("unavailable", f"Event Registry error: {str(data['error'])[:200]}")
    return data if isinstance(data, dict) else {}


def _lang_val(v: Any, lang: str) -> Any:
    """ER multilingual fields are {"eng": ...}; fall back to any language."""
    if isinstance(v, dict) and v and all(isinstance(k, str) and len(k) == 3 for k in v):
        return v.get(lang) or next(iter(v.values()))
    return v


def _article(a: dict[str, Any]) -> dict[str, Any]:
    src = a.get("source") or {}
    return {
        "url": a.get("url"),
        "title": one_line(a.get("title")),
        "outlet": src.get("title") or src.get("uri"),
        "date": a.get("dateTimePub") or a.get("dateTime") or a.get("date"),
        "body_excerpt": one_line(a.get("body"))[:300] or None,
    }


def fetch_events(
    params: dict[str, Any],
    at: datetime,
    *,
    api_key: str | None = None,
    post: Post = http_post,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Raw event dicts, biggest first; each gets `_top_articles` if `params.top_articles` > 0."""
    key = api_key if api_key is not None else api_key_from_env()
    if not key:
        raise ToolError("unavailable", "Event Registry is not configured (EVENT_REGISTRY_API_KEY not set)")
    try:
        data = _check(post(GET_EVENTS, build_events_body(params, at, key, now=now)))
        events = list((data.get("events") or {}).get("results") or [])
        n_top = int(params.get("top_articles", 0) or 0)
        for ev in events[: int(params.get("top_articles_events", 5))] if n_top > 0 else []:
            body = {
                "apiKey": key,
                "eventUri": ev.get("uri"),
                "resultType": "articles",
                "articlesCount": min(100, n_top),
                "articlesSortBy": params.get("top_articles_sort", "cosSim"),
                "articleBodyLen": 300,
                "lang": params.get("lang", "eng"),
            }
            res = _check(post(GET_EVENT, body))
            ev["_top_articles"] = ((res.get(ev.get("uri")) or {}).get("articles") or {}).get("results") or []
    except httpx.HTTPError as e:
        raise ToolError("unavailable", f"Event Registry request failed ({type(e).__name__})") from e
    return events


def _event_fields(ev: dict[str, Any], lang: str) -> dict[str, Any]:
    info = _lang_val(ev.get("infoArticle") or {}, lang) or {}
    info = info if isinstance(info, dict) else {}
    top = [_article(a) for a in ev.get("_top_articles") or []]
    info_a = _article(info) if info.get("url") else None
    articles = ([info_a] if info_a else []) + [a for a in top if not info_a or a["url"] != info_a["url"]]
    outlets = list(dict.fromkeys(a["outlet"] for a in articles if a.get("outlet")))
    uri = str(ev.get("uri") or "")
    return {
        "uri": uri,
        "title": one_line(_lang_val(ev.get("title"), lang)),
        "summary": one_line(_lang_val(ev.get("summary"), lang)),
        "url": info_a["url"] if info_a else (top[0]["url"] if top and top[0]["url"] else None),
        "articles": articles,
        "outlets": outlets,
        "article_count": ev.get("totalArticleCount"),
        "article_counts": ev.get("articleCounts"),
        "event_date": ev.get("eventDate"),
        "published": parse_iso(info.get("dateTimePub") or info.get("dateTime")) if info else None,
        "categories": [c.get("label") or c.get("uri") for c in (ev.get("categories") or [])[:3]],
    }


def events_to_observations(
    events: list[dict[str, Any]],
    *,
    source_id: str,
    beat: str,
    at: datetime,
    lang: str = "eng",
    now: datetime | None = None,
) -> list[HeadlineObservation]:
    """Live (`at` within an hour of now): observed_at = at, rank = position by size, counts in `extra`.
    Past `at`: the listing is hindsight (counts include later articles), so, as for other
    `hindsight_listing` sources, observed_at = fetch time, rank = None, and position / counts go to
    `extra["hindsight"]`."""
    at = _utc(at)
    now = _utc(now or datetime.now(UTC))
    live = (now - at) <= timedelta(hours=1)
    out = []
    for ev in events:
        f = _event_fields(ev, lang)
        if not f["title"]:
            continue
        url = f["url"] or f"{ER_BASE}/event/{f['uri']}"
        published = f["published"]
        if published is None and f["event_date"]:
            try:
                published = datetime.combine(datetime.fromisoformat(f["event_date"]).date(), time(0), UTC)
            except ValueError:
                published = None
        position = len(out) + 1
        counts = {"article_count": f["article_count"], "article_counts": f["article_counts"]}
        out.append(
            HeadlineObservation(
                source_id=source_id,
                beat=beat,
                observed_at=at if live else now,
                url=url,
                title=f["title"][:300],
                rank=position if live else None,
                snippet=(f["summary"][:600] or None),
                published_at=published,
                external_id=f["uri"] or None,
                provenance="live" if live else "hindsight_listing",
                extra={
                    **(counts if live else {}),
                    "hindsight": None
                    if live
                    else {
                        "api_position": position,
                        **counts,
                        "fetched_at": now.isoformat(),
                        "window_end": at.isoformat(),
                    },
                    "event_uri": f["uri"],
                    "event_date": f["event_date"],
                    "outlets": f["outlets"],
                    "top_articles": f["articles"],
                    "categories": f["categories"],
                    "url_is_event_page": f["url"] is None,
                    "rank_rule": "eventsSortBy=size (article count at fetch time)",
                },
            )
        )
    return out


def observe_events(
    cfg: HeadlineSourceCfg,
    beat: str,
    at: datetime,
    *,
    post: Post = http_post,
    api_key: str | None = None,
    now: datetime | None = None,
) -> list[HeadlineObservation]:
    events = fetch_events(cfg.params, at, api_key=api_key, post=post, now=now)
    return events_to_observations(
        events, source_id=cfg.id, beat=beat, at=at, lang=cfg.params.get("lang", "eng"), now=now
    )


def events_to_ranked(events: list[dict[str, Any]], lang: str = "eng") -> list[RankedStory]:
    fields = [f for f in (_event_fields(ev, lang) for ev in events) if f["title"]]
    biggest = max((f["article_count"] or 0 for f in fields), default=0) or 1
    out = []
    for f in fields:
        n = f["article_count"] or 0
        out.append(
            RankedStory(
                rank=len(out) + 1,
                headline=f["title"],
                summary=f["summary"],
                urls=[a["url"] for a in f["articles"] if a.get("url")],
                outlets=f["outlets"],
                importance=round(100 * n / biggest),  # relative to the biggest event in this result
                reason=f"{n} articles in the event cluster",
                extra={"event_uri": f["uri"], "article_count": n, "event_date": f["event_date"]},
            )
        )
    return out


async def eventregistry_ranked(
    beat_cfg: BeatCfg | str,
    at: datetime,
    params: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
    post: Post = http_post,
) -> RankedStories:
    """Event Registry's own ranking (by event size) for the beat in the 24 h before `at`.

    `params` defaults to the `params` of the beat's first `eventregistry` headline source, if it has one.
    `.usage` is {"requests": n}; ER reports no per-call token use in the response.
    """
    beat = loader.beats()[beat_cfg] if isinstance(beat_cfg, str) else beat_cfg
    if params is None:
        params = next((s.params for s in beat.headline_sources if s.type == "eventregistry"), {})
    n_calls = 1
    events = await asyncio.to_thread(fetch_events, params, at, api_key=api_key, post=post)
    if params.get("top_articles"):
        n_calls += min(len(events), int(params.get("top_articles_events", 5)))
    end = _utc(at)
    return RankedStories(
        events_to_ranked(events, params.get("lang", "eng")),
        usage={"requests": n_calls},
        meta={
            "provider": "eventregistry",
            "beat": beat.id,
            "window": [(end - timedelta(hours=24)).isoformat(), end.isoformat()],
            "date_range": [(end - timedelta(hours=24)).date().isoformat(), end.date().isoformat()],
            "hindsight": datetime.now(UTC) - end > timedelta(hours=1),
            "params": {k: v for k, v in params.items() if k != "apiKey"},
        },
    )
