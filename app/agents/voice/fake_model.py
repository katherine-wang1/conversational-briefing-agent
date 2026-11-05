"""Fake `ModelClient`s for the voice server (no network, no spend).

- `ScriptedModel`: replays scripted turns (text and/or tool calls), records requests. For tests.
- `CorpusEchoModel`: a rule-based stand-in for local dev before the real model layer (W-B) is wired.
  It is NOT an agent: it reads back the first fact of the story in question, with its citation, and
  maps a few keywords to tools. Use `VOICE_MODEL=fake` to enable it.
- `UnavailableModel`: what the server uses when no model is configured; every turn errors clearly.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from itertools import count
from typing import Any

from app.contracts.llm import ChatRequest, ChatResponse, StreamEvent, ToolCall, Usage

_ids = count(1)


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(id=f"call_{next(_ids)}", name=name, arguments=args)


class ScriptedModel:
    """Each element of `script` is one model call: a str (text), a ToolCall, or a list mixing both."""

    def __init__(self, script: list[Any], *, chunk: int = 7, delay_s: float = 0.0):
        self.script = list(script)
        self.requests: list[ChatRequest] = []
        self.chunk = chunk
        self.delay_s = delay_s

    async def chat(self, req: ChatRequest) -> ChatResponse:
        text, calls = "", []
        async for ev in self.stream(req):
            if ev.type == "text_delta":
                text += ev.text or ""
            elif ev.type == "tool_call" and ev.tool_call:
                calls.append(ev.tool_call)
        return ChatResponse(model="scripted", provider="fake", content=text, tool_calls=calls)

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(req)
        step = self.script.pop(0) if self.script else "I don't have anything more on that."
        items = step if isinstance(step, list) else [step]
        text = ""
        calls = []
        for it in items:
            if isinstance(it, ToolCall):
                calls.append(it)
                yield StreamEvent(type="tool_call", tool_call=it)
            else:
                for i in range(0, len(it), self.chunk):
                    if self.delay_s:
                        await asyncio.sleep(self.delay_s)
                    yield StreamEvent(type="text_delta", text=it[i : i + self.chunk])
                text += it
        resp = ChatResponse(
            model="scripted",
            provider="fake",
            content=text,
            tool_calls=calls,
            usage=Usage(tokens_in=100, tokens_out=len(text) // 4),
            finish_reason="stop",
        )
        yield StreamEvent(type="done", response=resp)


class CorpusEchoModel:
    """Dev-only stand-in (see module docstring)."""

    async def chat(self, req: ChatRequest) -> ChatResponse:
        return ChatResponse(model="corpus-echo", provider="fake", content="")

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        last = req.messages[-1]
        if last.role == "tool":
            text = "Okay." if "<tool_error" not in last.content else "I couldn't do that just now."
            if "<untrusted_source" in last.content:
                m = re.search(r"<untrusted_source[^>]*>\n(.*?)\n", last.content)
                text = f"Here's what I found: {m.group(1)}." if m else "I found nothing useful."
            yield StreamEvent(type="text_delta", text=text)
            yield StreamEvent(
                type="done", response=ChatResponse(model="corpus-echo", provider="fake", content=text)
            )
            return
        utter = last.content.rsplit("Listener:", 1)[-1].strip().lower()
        story = re.search(r"story_id=([^\s,')]+)", last.content)
        sid = story.group(1) if story else None
        call = None
        if utter.startswith(("search", "look up", "check")):
            call = _call("quick_search", query=utter.split(" ", 1)[-1])
        elif "remind" in utter or "follow up" in utter or "bookmark" in utter:
            call = _call("bookmark_question", question=utter, **({"story_id": sid} if sid else {}))
        elif "save" in utter and sid:
            call = _call("save_for_later", story_id=sid)
        elif utter in ("next", "skip", "skip it", "next story"):
            call = _call("navigate", action="next_story")
        elif utter in ("stop", "end", "that's all", "goodbye"):
            call = _call("navigate", action="end_session")
        if call:
            yield StreamEvent(type="tool_call", tool_call=call)
            yield StreamEvent(
                type="done", response=ChatResponse(model="corpus-echo", provider="fake", tool_calls=[call])
            )
            return
        corpus = req.messages[1].content if len(req.messages) > 1 else ""
        fact = None
        if sid:
            sec = corpus.split(f"## story_id={sid}:", 1)
            if len(sec) == 2:
                fact = re.search(r"- fact: (.*?) \[([^\]]*)\]", sec[1])
        if fact:
            cites = " ".join(f"[{c.strip()}]" for c in fact.group(2).split(","))
            text = f"From the sources: {fact.group(1)} {cites} That's what I have on it."
        else:
            text = "I don't have that in today's sources. Want me to look into it for tomorrow?"
        for w in re.findall(r"\S+\s*", text):
            yield StreamEvent(type="text_delta", text=w)
        yield StreamEvent(
            type="done", response=ChatResponse(model="corpus-echo", provider="fake", content=text)
        )


class UnavailableModel:
    async def chat(self, req: ChatRequest) -> ChatResponse:
        raise RuntimeError("no model client configured")

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="error", error="no model client configured (model layer not wired)")


def tool_call(name: str, **args: Any) -> ToolCall:
    return _call(name, **args)
