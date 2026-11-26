"""SEARCH_PROVIDER=anthropic: request shape, result parsing, pause_turn, error objects, missing key."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.contracts.common import TokenBudget, TraceCtx
from app.contracts.tools import SearchArgs, ToolContext
from app.llm.anthropic_api import AnthropicUsage, extract_json
from app.llm.ledger import Ledger, MemoryLedgerSink
from app.tools import build_tools
from app.tools.search import AnthropicSearchProvider, LiveSearch, build_provider, parse_page_age
from tests.unit.sources.anthropic_http import (
    FakeAnthropic,
    message,
    search_call,
    search_error,
    search_result,
    text,
)

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
SINCE = datetime(2026, 11, 25, 12, tzinfo=UTC)

R1 = {"url": "https://example.com/a", "title": "Acme ships X", "page_age": "November 26, 2026"}
R2 = {"url": "https://news.example.org/b", "title": "Old story", "page_age": "March 2, 2024"}
R3 = {"url": "https://blog.example.net/c", "title": "Third", "page_age": "3 hours ago"}


def ctx() -> ToolContext:
    return ToolContext(mode="live", now=NOW, budget=TokenBudget(input_tokens=10_000), trace=TraceCtx())


def provider(fake: FakeAnthropic, **kw) -> AnthropicSearchProvider:  # noqa: ANN003
    return AnthropicSearchProvider("test-key", anthropic_client=fake.client, **kw)


def answer(items: list[dict]) -> dict:
    return text(json.dumps({"results": items}))


async def test_request_shape_and_parsing():
    reply = answer(
        [
            {
                "url": R3["url"],
                "title": "Third (model title)",
                "outlet": "Example Blog",
                "published": None,
                "summary": "Third thing happened.",
            },
            {"url": "https://invented.example.com/z", "title": "Made up", "summary": "never seen"},
            {
                "url": R1["url"] + "/",
                "outlet": "Example",
                "published": "2026-11-26",
                "summary": "Acme shipped X.",
            },
        ]
    )
    fake = FakeAnthropic(
        [message([text("Searching."), search_call("acme x"), search_result([R1, R2, R3]), reply], searches=1)]
    )
    ledger = Ledger([MemoryLedgerSink()])
    p = provider(fake, model="claude-opus-5-5", max_uses=4, ledger=ledger)
    hits = await p.search(SearchArgs(query="acme x", since=SINCE, max_results=5))

    body = fake.requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["tools"] == [{"type": "web_search_20260318", "name": "web_search", "max_uses": 4}]
    assert body["fallbacks"] == "default" and "thinking" not in body
    assert body["output_config"] == {"effort": "low"}
    assert "server-side-fallback-2026-07-01" in fake.headers[0]["anthropic-beta"]
    assert "acme x" in body["messages"][0]["content"] and SINCE.isoformat() in body["messages"][0]["content"]
    assert "at most 5 results" in body["system"]

    # model order; invented URL dropped; trailing-slash URL matched to the real result
    assert [h.url for h in hits] == [R3["url"], R1["url"]]
    assert hits[0].title == "Third (model title)" and hits[0].source == "Example Blog"
    assert hits[0].snippet == "Third thing happened."
    assert hits[1].published_at == datetime(2026, 11, 26, tzinfo=UTC)
    rec = ledger.sinks[0].records[0]
    assert rec.provider == "anthropic" and rec.role == "search.anthropic"
    assert rec.cost_usd == round((1000 * 4 + 200 * 20) / 1e6 + 0.01, 6)


async def test_no_json_falls_back_to_results_with_cited_text_and_date_filter():
    cite = {
        "type": "web_search_result_location",
        "url": R1["url"],
        "title": R1["title"],
        "encrypted_index": "x",
        "cited_text": "Acme today shipped X, its...",
    }
    fake = FakeAnthropic(
        [message([search_call("q"), search_result([R1, R2]), text("Acme shipped X.", [cite])], searches=1)]
    )
    hits = await provider(fake).search(SearchArgs(query="q", since=SINCE))
    assert [h.url for h in hits] == [R1["url"]]  # R2 (2024) is outside the window
    assert hits[0].snippet == "Acme today shipped X, its..."
    assert hits[0].title == "Acme ships X" and hits[0].source == "example.com"


async def test_pause_turn_resends_assistant_content():
    first = message([search_call("q"), search_result([R1])], stop_reason="pause_turn", searches=1)
    second = message([answer([{"url": R1["url"], "summary": "s"}])], input_tokens=50, output_tokens=10)
    fake = FakeAnthropic([first, second])
    p = provider(fake)
    hits = await p.search(SearchArgs(query="q"))
    assert [h.url for h in hits] == [R1["url"]]
    cont = fake.requests[1]["messages"]
    assert cont[0]["role"] == "user" and cont[1]["role"] == "assistant"
    assert [b["type"] for b in cont[1]["content"]] == ["server_tool_use", "web_search_tool_result"]
    assert cont[1]["content"][1]["content"][0]["encrypted_content"] == "enc"  # sent back unchanged
    assert fake.requests[1]["tools"] == fake.requests[0]["tools"]
    assert p.last_usage.requests == 2 and p.last_usage.input_tokens == 1050


async def test_pause_turn_is_bounded():
    paused = message([search_call("q"), search_result([R1])], stop_reason="pause_turn")
    fake = FakeAnthropic([paused, paused, paused])
    p = provider(fake, max_continuations=2)
    hits = await p.search(SearchArgs(query="q"))
    assert len(fake.requests) == 3 and p.last_usage.stop_reason == "pause_turn"
    assert [h.url for h in hits] == [R1["url"]]  # partial results still usable


async def test_error_object_only_is_unavailable():
    fake = FakeAnthropic([message([search_call("q"), search_error("too_many_requests"), text("Sorry.")])])
    r = await build_search(fake)(ctx(), query="q")
    assert not r.ok and r.error.startswith("search_unavailable") and "too_many_requests" in r.error


async def test_error_object_next_to_results_keeps_results():
    fake = FakeAnthropic(
        [
            message(
                [
                    search_call("q"),
                    search_result([R1]),
                    search_call("q2", "srvtoolu_2"),
                    search_error("max_uses_exceeded", "srvtoolu_2"),
                    text("done"),
                ]
            )
        ]
    )
    hits = await provider(fake).search(SearchArgs(query="q"))
    assert [h.url for h in hits] == [R1["url"]]


async def test_empty_results_list_is_empty_not_error():
    fake = FakeAnthropic([message([search_call("q"), search_result([]), text('{"results": []}')])])
    assert await provider(fake).search(SearchArgs(query="q")) == []


async def test_refusal_is_unavailable_and_recorded():
    fake = FakeAnthropic(
        [
            message(
                [],
                stop_reason="refusal",
                stop_details={"type": "refusal", "category": "cyber", "explanation": None},
            )
        ]
    )
    ledger = Ledger([MemoryLedgerSink()])
    r = await build_search(fake, ledger=ledger)(ctx(), query="q")
    assert not r.ok and "refusal" in r.error
    assert ledger.sinks[0].records[0].ok is False


async def test_http_errors_map_to_unavailable():
    fake = FakeAnthropic(
        [(401, {"type": "error", "error": {"type": "authentication_error", "message": "x"}})]
    )
    r = await build_search(fake)(ctx(), query="q")
    assert not r.ok and r.error.startswith("search_unavailable") and "API key" in r.error
    fake = FakeAnthropic([(529, {"type": "error", "error": {"type": "overloaded_error", "message": "x"}})])
    r = await build_search(fake)(ctx(), query="q")
    assert not r.ok and "HTTP 529" in r.error


async def test_missing_key_is_unavailable_never_fake():
    tools = build_tools(search_provider="anthropic", search_api_key="")
    r = await tools["search"](ctx(), query="anything")
    assert not r.ok and r.error.startswith("search_unavailable")
    assert build_provider("anthropic", "") is None


async def test_build_tools_reads_anthropic_settings():
    from app.config.settings import Settings

    s = Settings(
        search_provider="anthropic",
        anthropic_api_key="k",
        anthropic_search_model="claude-sonnet-5-5",
        anthropic_search_max_uses=2,
        _env_file=None,
    )
    tools = build_tools(s)
    p = tools["search"].live.provider
    assert isinstance(p, AnthropicSearchProvider)
    assert (p.api_key, p.model, p.max_uses) == ("k", "claude-sonnet-5-5", 2)


def test_parse_page_age_formats():
    assert parse_page_age("April 30, 2025") == datetime(2025, 4, 30, tzinfo=UTC)
    assert parse_page_age("2026-11-20T08:00:00Z") == datetime(2026, 11, 20, 8, tzinfo=UTC)
    assert parse_page_age("2 days ago", NOW) == datetime(2026, 11, 24, 12, tzinfo=UTC)
    assert parse_page_age("sometime") is None and parse_page_age(None) is None


def test_extract_json_with_prose_and_fences():
    assert extract_json('Here you go:\n```json\n{"results": [{"url": "u"}]}\n```') == {
        "results": [{"url": "u"}]
    }
    assert extract_json("no json [here") is None


def test_usage_cost_unknown_model():
    u = AnthropicUsage(model="some-other-model", web_search_requests=3)
    assert u.cost_usd == 0.03 and not u.cost_known


def build_search(fake: FakeAnthropic, **kw):  # noqa: ANN003, ANN201
    from app.tools.search import ReplaySearch, SearchTool

    return SearchTool(LiveSearch(provider(fake, **kw)), ReplaySearch(None))
