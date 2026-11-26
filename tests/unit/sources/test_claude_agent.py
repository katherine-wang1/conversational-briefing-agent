"""Claude news-agent reference arm: request, streamed parsing, URL check, repair, pause_turn, errors."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.config import loader
from app.llm.ledger import Ledger, MemoryLedgerSink
from app.sources.claude_agent import RankedStory, claude_agent_digest
from app.tools.base import ToolError
from tests.unit.sources.anthropic_http import (
    FakeAnthropic,
    fetch_result,
    message,
    search_call,
    search_result,
    text,
)

AT = datetime(2026, 11, 26, 12, tzinfo=UTC)
R1 = {"url": "https://example.com/a", "title": "Lab releases model"}
R2 = {"url": "https://example.org/b", "title": "Chip export rule"}


def story(rank: int, url: str, **kw) -> dict:  # noqa: ANN003
    return {
        "rank": rank,
        "headline": f"Story {rank}",
        "summary": "s",
        "urls": [url],
        "outlets": ["Ex"],
        "importance": 80,
        "reason": "r",
        **kw,
    }


def turn(stories: list[dict], *extra: dict, stop_reason: str = "end_turn") -> dict:
    return message(
        [
            search_call("ai news"),
            search_result([R1, R2]),
            *extra,
            text("Here is the ranking:\n" + json.dumps({"stories": stories})),
        ],
        stop_reason=stop_reason,
        searches=3,
        fetches=1,
    )


async def test_request_and_parse():
    fake = FakeAnthropic(
        [
            turn(
                [
                    story(2, R2["url"]),
                    story(1, R1["url"], importance=140),
                    story(
                        3,
                        "https://made-up.example/x",
                        urls=["https://made-up.example/x", "https://fetched.example/p"],
                    ),
                ],
                search_call("fetch", "srvtoolu_f1")
                | {"name": "web_fetch", "input": {"url": "https://fetched.example/p"}},
                fetch_result("https://fetched.example/p"),
            )
        ]
    )
    ledger = Ledger([MemoryLedgerSink()])
    out = await claude_agent_digest(
        loader.beats()["ai"], AT, max_searches=7, top_k=2, client=fake.client, ledger=ledger
    )
    body = fake.requests[0]
    assert body["stream"] is True and body["model"] == "claude-opus-5-5"
    assert body["tools"] == [
        {"type": "web_search_20260318", "name": "web_search", "max_uses": 7},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10},
    ]
    assert body["output_config"] == {"effort": "high"} and body["fallbacks"] == "default"
    assert "thinking" not in body and "tool_choice" not in body
    prompt = body["messages"][0]["content"]
    beat = loader.beats()["ai"]
    assert beat.name in prompt and beat.subtopics[0] in prompt and beat.boundaries[0] in prompt
    assert "2026-11-25T12:00:00+00:00 to 2026-11-26T12:00:00+00:00" in prompt

    assert all(isinstance(s, RankedStory) for s in out)
    assert [(s.rank, s.urls) for s in out] == [(1, [R1["url"]]), (2, [R2["url"]])]  # sorted, cut to top_k
    assert out[0].importance == 100  # clamped
    assert out.usage.web_search_requests == 3 and out.usage.cost_usd > 0.03
    assert out.meta["hindsight"] is True and out.meta["repaired"] is False
    assert ledger.sinks[0].records[0].role == "reference.claude_agent.ai"


async def test_unseen_urls_are_dropped():
    fake = FakeAnthropic(
        [
            message(
                [
                    search_call("q"),
                    search_result([R1]),
                    fetch_result("https://fetched.example/p"),
                    text(
                        json.dumps(
                            {
                                "stories": [
                                    story(
                                        1,
                                        R1["url"],
                                        urls=[
                                            R1["url"],
                                            "https://fetched.example/p",
                                            "https://invented.example/z",
                                        ],
                                    )
                                ]
                            }
                        )
                    ),
                ]
            )
        ]
    )
    out = await claude_agent_digest("ai", AT, client=fake.client)
    assert out[0].urls == [R1["url"], "https://fetched.example/p"]
    assert out.meta["dropped_unseen_urls"] == 1


async def test_invalid_json_gets_one_structured_repair():
    fake = FakeAnthropic(
        [
            message(
                [search_call("q"), search_result([R1]), text("1. Lab releases model (example.com/a) - big")]
            ),
            message(
                [text(json.dumps({"stories": [story(1, R1["url"])]}))], input_tokens=100, output_tokens=50
            ),
        ]
    )
    out = await claude_agent_digest("ai", AT, client=fake.client)
    assert out.meta["repaired"] is True and out[0].urls == [R1["url"]]
    repair = fake.requests[1]
    assert "tools" not in repair
    assert repair["output_config"]["format"]["type"] == "json_schema"
    assert "Lab releases model" in repair["messages"][0]["content"]
    assert out.usage.requests == 2


async def test_repair_failure_raises_invalid_output():
    fake = FakeAnthropic(
        [
            message([search_call("q"), search_result([R1]), text("not json")]),
            message([text('{"stories": "nope"}')]),
        ]
    )
    with pytest.raises(ToolError) as e:
        await claude_agent_digest("ai", AT, client=fake.client)
    assert e.value.code == "invalid_output"


async def test_pause_turn_continues_then_parses():
    first = message([search_call("q"), search_result([R1])], stop_reason="pause_turn")
    second = message([text(json.dumps({"stories": [story(1, R1["url"])]}))])
    fake = FakeAnthropic([first, second])
    out = await claude_agent_digest("ai", AT, client=fake.client)
    assert len(fake.requests) == 2 and fake.requests[1]["messages"][-1]["role"] == "assistant"
    assert out[0].urls == [R1["url"]]  # result block from the paused response still counts as seen


async def test_refusal_raises():
    fake = FakeAnthropic([message([], stop_reason="refusal")])
    with pytest.raises(ToolError) as e:
        await claude_agent_digest("ai", AT, client=fake.client)
    assert e.value.code == "refusal"


async def test_missing_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    with pytest.raises(ToolError) as e:
        await claude_agent_digest("ai", AT)
    assert e.value.code == "unavailable"
