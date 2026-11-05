"""Contract: live and replay tool backends return identical shapes (TDD §24).

Live backends run against mocked HTTP here. `test_live_search_real_network` hits the real provider
and is skipped unless SEARCH_PROVIDER / SEARCH_API_KEY are set and LIVE_TOOL_TESTS=1.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.sources import HeadlineObservation
from app.contracts.tools import (
    CommentsResult,
    FetchResult,
    HeadlineSourceResult,
    SearchResult,
    ToolContext,
    ToolResult,
)
from app.tools import build_tools
from app.tools.fetch import InMemoryDocumentCache
from app.tools.headlines import register_source_adapter
from tests.unit.platform.archive_fixture import U1, build_fixture_archive

NOW = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
PAGE = (
    "<html><body><article><h1>Acme</h1><p>Acme Corp unveiled Voltaic, a solid-state battery cell for grid "
    "storage, at an event on Tuesday where executives described pilot customers.</p><p>The company raised "
    "forty million dollars from investors to build a pilot line in Nevada next year.</p>"
    "</article></body></html>"
)


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.tavily.com":
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Acme Voltaic",
                        "url": U1,
                        "content": "Acme …",
                        "published_date": "2026-09-30T20:00:00Z",
                    }
                ]
            },
        )
    if request.url.host == "hn.algolia.com":
        return httpx.Response(
            200,
            json={
                "children": [
                    {"id": 7, "created_at": "2026-10-01T05:10:00Z", "text": "Energy density?", "points": None}
                ]
            },
        )
    return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})


async def _fake_adapter(cfg, beat, at):  # noqa: ANN001
    return [HeadlineObservation(source_id=cfg.id, beat=beat, observed_at=at, url=U1, title="Acme", rank=1)]


def _shape(v):  # noqa: ANN001, ANN202
    """Structural signature: model class + field names, recursively. Scalars (and None, for optional
    fields) are wildcards: values may legitimately differ between backends, shapes may not."""
    if isinstance(v, BaseModel):
        return (type(v).__name__, tuple((k, _shape(getattr(v, k))) for k in type(v).model_fields))
    if isinstance(v, list):
        return ("list", _shape(v[0]) if v else None)
    return "*"


CALLS = [
    ("search", {"query": "Voltaic battery"}, SearchResult),
    ("fetch", {"url": U1}, FetchResult),
    ("headline_source", {"source_id": "techmeme", "at": NOW}, HeadlineSourceResult),
    ("hn_comments", {"item_id": "4242", "limit": 3}, CommentsResult),
]


@pytest.mark.parametrize(("tool", "args", "result_type"), CALLS)
async def test_live_and_replay_return_identical_shapes(tmp_path, tool, args, result_type):
    register_source_adapter("techmeme", _fake_adapter)
    tools = build_tools(
        archive=build_fixture_archive(tmp_path / "a.sqlite"),
        search_provider="tavily",
        search_api_key="k",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        doc_cache=InMemoryDocumentCache(),
        check_urls=False,
    )
    out = {}
    for mode in ("live", "replay"):
        ctx = ToolContext(mode=mode, now=NOW, budget=TokenBudget(input_tokens=1000), trace=TraceCtx())
        r = await tools[tool](ctx, **args)
        assert isinstance(r, ToolResult) and r.ok, (mode, r.error)
        assert isinstance(r.data, result_type)
        assert "<untrusted_source" in r.as_untrusted_text()
        out[mode] = r
    live, replay = out["live"].data, out["replay"].data
    assert _shape(live) == _shape(replay)
    assert set(type(live).model_fields) == set(type(replay).model_fields)


@pytest.mark.skipif(
    not (os.environ.get("SEARCH_API_KEY") and os.environ.get("LIVE_TOOL_TESTS") == "1"),
    reason="live search key not set (or LIVE_TOOL_TESTS!=1)",
)
async def test_live_search_real_network():
    tools = build_tools()
    ctx = ToolContext(
        mode="live", now=datetime.now(UTC), budget=TokenBudget(input_tokens=1000), trace=TraceCtx()
    )
    r = await tools["search"](ctx, query="OpenAI", max_results=3)
    assert r.ok, r.error
    assert isinstance(r.data, SearchResult)
