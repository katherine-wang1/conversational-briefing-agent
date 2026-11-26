"""A real `anthropic.AsyncAnthropic` over a mocked httpx2 transport: scripted Messages API responses.

Non-streaming requests get the message JSON; streaming requests (`"stream": true`) get the same message
as server-sent events, so the SDK's own stream accumulation (`get_final_message`) is exercised.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")


def message(
    content: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-opus-5-5",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    searches: int = 0,
    fetches: int = 0,
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "server_tool_use": {"web_search_requests": searches, "web_fetch_requests": fetches},
        },
    }


def text(t: str, citations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "text", "text": t}
    if citations:
        b["citations"] = citations
    return b


def search_call(query: str, id_: str = "srvtoolu_1") -> dict[str, Any]:
    return {"type": "server_tool_use", "id": id_, "name": "web_search", "input": {"query": query}}


def search_result(results: list[dict[str, Any]] | dict[str, Any], id_: str = "srvtoolu_1") -> dict[str, Any]:
    if isinstance(results, list):
        content: Any = [
            {"type": "web_search_result", "encrypted_content": "enc", "page_age": None, **r} for r in results
        ]
    else:
        content = results
    return {"type": "web_search_tool_result", "tool_use_id": id_, "content": content}


def search_error(code: str, id_: str = "srvtoolu_1") -> dict[str, Any]:
    return search_result({"type": "web_search_tool_result_error", "error_code": code}, id_)


def fetch_result(url: str, id_: str = "srvtoolu_f1") -> dict[str, Any]:
    return {
        "type": "web_fetch_tool_result",
        "tool_use_id": id_,
        "content": {
            "type": "web_fetch_result",
            "url": url,
            "retrieved_at": "2026-11-26T10:00:00Z",
            "content": {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": "body"},
                "title": "t",
            },
        },
    }


def _sse(msg: dict[str, Any]) -> bytes:
    events: list[tuple[str, dict[str, Any]]] = []
    start = {**msg, "content": [], "stop_reason": None, "stop_details": None}
    start["usage"] = {**msg["usage"], "output_tokens": 1}
    events.append(("message_start", {"type": "message_start", "message": start}))
    for i, b in enumerate(msg["content"]):
        if b["type"] == "text":
            events.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "text_delta", "text": b["text"]},
                    },
                )
            )
            for c in b.get("citations") or []:
                events.append(
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": i,
                            "delta": {"type": "citations_delta", "citation": c},
                        },
                    )
                )
        elif b["type"] == "server_tool_use":
            events.append(
                (
                    "content_block_start",
                    {"type": "content_block_start", "index": i, "content_block": {**b, "input": {}}},
                )
            )
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])},
                    },
                )
            )
        else:
            events.append(
                ("content_block_start", {"type": "content_block_start", "index": i, "content_block": b})
            )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": msg["stop_reason"],
                    "stop_sequence": None,
                    "stop_details": msg.get("stop_details"),
                },
                "usage": msg["usage"],
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


class FakeAnthropic:
    """`.client` is a real AsyncAnthropic; `.requests` collects each request's JSON body."""

    def __init__(self, responses: list[dict[str, Any] | tuple[int, dict[str, Any]]]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            self.requests.append(body)
            self.headers.append(dict(request.headers))
            nxt = self.responses.pop(0)
            if isinstance(nxt, tuple):
                return httpx2.Response(nxt[0], json=nxt[1])
            if body.get("stream"):
                return httpx2.Response(200, content=_sse(nxt), headers={"content-type": "text/event-stream"})
            return httpx2.Response(200, json=nxt)

        self.client = anthropic.AsyncAnthropic(
            api_key="test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
