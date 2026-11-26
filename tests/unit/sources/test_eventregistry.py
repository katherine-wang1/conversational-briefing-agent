"""Event Registry adapter: request body, event -> HeadlineObservation / RankedStory mapping, errors."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.config.loader import HeadlineSourceCfg
from app.sources import SOURCE_TYPES
from app.sources.adapters import observe
from app.sources.eventregistry import (
    GET_EVENT,
    GET_EVENTS,
    build_events_body,
    eventregistry_ranked,
    observe_events,
)
from app.tools.base import ToolError

AT = datetime(2026, 11, 26, 12, tzinfo=UTC)

EV_BIG = {
    "uri": "eng-111",
    "eventDate": "2026-11-26",
    "totalArticleCount": 314,
    "articleCounts": {"eng": 314},
    "title": {"eng": "Lab releases a new frontier model"},
    "summary": {"eng": "  The lab released   a model.\n It is big. "},
    "categories": [
        {"uri": "dmoz/Computers/Artificial_Intelligence", "label": "dmoz/Computers/AI", "wgt": 40}
    ],
    "infoArticle": {
        "eng": {
            "uri": "8000001",
            "url": "https://example.com/model",
            "title": "Lab releases model",
            "dateTime": "2026-11-26T09:30:00Z",
            "source": {"uri": "example.com", "title": "Example News"},
        }
    },
}
EV_SMALL = {
    "uri": "eng-222",
    "eventDate": "2026-11-25",
    "totalArticleCount": 31,
    "title": {"eng": "Chip export rule"},
    "summary": {"eng": "A rule."},
}


def cfg(**params) -> HeadlineSourceCfg:  # noqa: ANN003
    return HeadlineSourceCfg(
        id="er_ai",
        type="eventregistry",
        tier=2,
        weight=0.5,
        params={"keywords": ["artificial intelligence", "LLM"], **params},
    )


class Poster:
    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, body: dict) -> httpx.Response:
        self.calls.append((url, body))
        return self.responses.pop(0)


def events_resp(*events: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "events": {
                "results": list(events),
                "totalResults": len(events),
                "page": 1,
                "count": 30,
                "pages": 1,
            }
        },
    )


def test_request_body():
    b = build_events_body(
        {
            "keywords": ["ai", "llm"],
            "concepts": ["http://en.wikipedia.org/wiki/X"],
            "categories": ["dmoz/Computers"],
            "min_articles": 5,
            "count": 99,
        },
        AT,
        "k",
        now=AT,
    )
    assert b["apiKey"] == "k" and b["resultType"] == "events" and b["lang"] == "eng"
    assert (b["dateStart"], b["dateEnd"]) == ("2026-11-25", "2026-11-26")
    assert b["eventsSortBy"] == "size" and b["eventsSortByAsc"] is False and b["eventsCount"] == 50
    assert b["keyword"] == ["ai", "llm"] and b["keywordOper"] == "or" and b["keywordLoc"] == "body,title"
    assert b["conceptUri"] == ["http://en.wikipedia.org/wiki/X"] and b["conceptOper"] == "or"
    assert b["categoryUri"] == ["dmoz/Computers"] and b["minArticlesInEvent"] == 5
    assert b["includeEventInfoArticle"] is True and b["forceMaxDataTimeWindow"] == 31
    old = build_events_body({}, datetime(2025, 1, 1, tzinfo=UTC), "k", now=AT)
    assert "forceMaxDataTimeWindow" not in old and "keyword" not in old


def test_observe_maps_events(monkeypatch):
    post = Poster(events_resp(EV_BIG, EV_SMALL))
    obs = observe_events(cfg(), "ai", AT, post=post, api_key="k", now=AT)
    assert post.calls[0][0] == GET_EVENTS == "https://eventregistry.org/api/v1/event/getEvents"
    a, b = obs
    assert (a.rank, b.rank) == (1, 2)
    assert a.source_id == "er_ai" and a.beat == "ai" and a.observed_at == AT and a.provenance == "live"
    assert a.title == "Lab releases a new frontier model"
    assert a.snippet == "The lab released a model. It is big."
    assert a.url == "https://example.com/model" and a.external_id == "eng-111"
    assert a.published_at == datetime(2026, 11, 26, 9, 30, tzinfo=UTC)
    assert a.engagement is None
    assert a.extra["article_count"] == 314 and a.extra["event_uri"] == "eng-111"
    assert a.extra["outlets"] == ["example.com"] and a.extra["url_is_event_page"] is False
    # no info article: event page URL, eventDate as published
    assert b.url == "https://eventregistry.org/event/eng-222" and b.extra["url_is_event_page"] is True
    assert b.published_at == datetime(2026, 11, 25, tzinfo=UTC) and b.extra["outlets"] == []


def test_past_at_is_hindsight():
    later = datetime(2026, 11, 28, tzinfo=UTC)
    obs = observe_events(cfg(), "ai", AT, post=Poster(events_resp(EV_BIG)), api_key="k", now=later)
    o = obs[0]
    assert o.provenance == "hindsight_listing" and o.rank is None and o.observed_at == later
    assert "article_count" not in o.extra
    assert o.extra["hindsight"]["api_position"] == 1 and o.extra["hindsight"]["article_count"] == 314


def test_top_articles_extra_call():
    arts = {
        "eng-111": {
            "articles": {
                "results": [
                    {
                        "url": "https://example.com/model",
                        "title": "dup of info article",
                        "source": {"title": "Example News"},
                    },
                    {
                        "url": "https://other.example/x",
                        "title": "Other take",
                        "body": "Body text " * 50,
                        "dateTime": "2026-11-26T10:00:00Z",
                        "source": {"uri": "other.example", "title": "Other"},
                    },
                ]
            }
        }
    }
    post = Poster(events_resp(EV_BIG, EV_SMALL), httpx.Response(200, json=arts))
    obs = observe_events(cfg(top_articles=5, top_articles_events=1), "ai", AT, post=post, api_key="k", now=AT)
    assert len(post.calls) == 2
    url, body = post.calls[1]
    assert url == GET_EVENT and body["eventUri"] == "eng-111" and body["resultType"] == "articles"
    assert body["articlesCount"] == 5 and body["apiKey"] == "k"
    top = obs[0].extra["top_articles"]
    assert [t["url"] for t in top] == ["https://example.com/model", "https://other.example/x"]
    assert len(top[1]["body_excerpt"]) <= 300
    assert obs[0].extra["outlets"] == ["example.com", "other.example"]


def test_observe_dispatches_eventregistry(monkeypatch):
    monkeypatch.setenv("EVENT_REGISTRY_API_KEY", "envkey")
    post = Poster(events_resp(EV_BIG, EV_SMALL))
    obs = observe(cfg(keyword_filter="chip"), "ai", AT, post=post)
    assert [o.title for o in obs] == ["Chip export rule"]
    assert post.calls[0][1]["apiKey"] == "envkey"
    assert "eventregistry" in SOURCE_TYPES


def test_missing_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("EVENT_REGISTRY_API_KEY", raising=False)
    monkeypatch.setattr("app.sources.eventregistry.api_key_from_env", lambda: "")
    post = Poster()
    with pytest.raises(ToolError) as e:
        observe(cfg(), "ai", AT, post=post)
    assert e.value.code == "unavailable" and post.calls == []


@pytest.mark.parametrize(
    ("resp", "needle"),
    [
        (httpx.Response(401, text="bad key"), "HTTP 401"),
        (httpx.Response(429, text="slow"), "429"),
        (httpx.Response(200, json={"error": "Invalid concept uri"}), "Invalid concept uri"),
        (httpx.Response(200, text="<html>"), "invalid JSON"),
    ],
)
def test_errors_are_unavailable(resp, needle):
    with pytest.raises(ToolError) as e:
        observe_events(cfg(), "ai", AT, post=Poster(resp), api_key="k")
    assert e.value.code == "unavailable" and needle in e.value.message


def test_network_error_is_unavailable():
    def boom(url, body):  # noqa: ANN001, ANN202
        raise httpx.ConnectTimeout("t")

    with pytest.raises(ToolError) as e:
        observe_events(cfg(), "ai", AT, post=boom, api_key="k")
    assert e.value.code == "unavailable"


async def test_ranked():
    out = await eventregistry_ranked(
        "ai", AT, {"keywords": "AI"}, api_key="k", post=Poster(events_resp(EV_BIG, EV_SMALL))
    )
    assert [(s.rank, s.importance) for s in out] == [(1, 100), (2, 10)]
    assert out[0].urls == ["https://example.com/model"] and out[0].outlets == ["example.com"]
    assert out[0].extra == {"event_uri": "eng-111", "article_count": 314, "event_date": "2026-11-26"}
    assert out.meta["provider"] == "eventregistry" and out.meta["date_range"] == ["2026-11-25", "2026-11-26"]
    assert out.usage == {"requests": 1, "tokens": 0.0}


async def test_live_headline_tool_uses_adapter(monkeypatch):
    """The `headline_source` tool reaches the eventregistry adapter for a configured source id."""
    from app.contracts.common import TokenBudget, TraceCtx
    from app.contracts.tools import ToolContext
    from app.tools import build_tools, headlines

    beat_cfg = cfg()
    monkeypatch.setattr(headlines, "source_config", lambda sid: ("ai", beat_cfg))
    monkeypatch.setattr("app.sources.eventregistry.api_key_from_env", lambda: "")
    monkeypatch.delenv("EVENT_REGISTRY_API_KEY", raising=False)
    tools = build_tools(search_provider="", search_api_key="")
    ctx = ToolContext(mode="live", now=AT, budget=TokenBudget(input_tokens=1000), trace=TraceCtx())
    r = await tools["headline_source"](ctx, source_id="er_ai", at=AT)
    assert not r.ok and r.error.startswith("unavailable") and "EVENT_REGISTRY_API_KEY" in r.error


async def test_ranked_counts_tokens_from_headers():
    ev = httpx.Response(200, json={"events": {"results": [EV_BIG]}}, headers={"req-tokens": "5.000"})
    arts = httpx.Response(
        200, json={"eng-111": {"articles": {"results": []}}}, headers={"req-tokens": "1.000"}
    )
    out = await eventregistry_ranked(
        "ai",
        AT,
        {"keywords": "AI", "top_articles": 3, "top_articles_events": 1},
        api_key="k",
        post=Poster(ev, arts),
    )
    assert out.usage == {"requests": 2, "tokens": 6.0}
