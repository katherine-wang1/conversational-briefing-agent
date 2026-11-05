"""Fakes for tests and dry runs. No network, no model calls.

- `FakeModelClient`: a `ModelClient` whose replies come from a Python responder per role.
- `FakeVoiceAgent`: a scripted text-mode voice agent speaking the session protocol.
- `FakeAgent`: implements `AgentEntryPoints` (eval/runner/interfaces.py) over a tiny synthetic
  world so the runner skeleton can be exercised end to end. All fake content is obviously synthetic.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel

from app.contracts.brief import BriefScript, BriefSentence, BriefStory, Passage, SourcePack
from app.contracts.digest import Digest, DigestEntry
from app.contracts.digest import StoryFeatures as Features
from app.contracts.llm import ChatRequest, ChatResponse, StreamEvent, Usage
from app.contracts.memory import Interest, MemorySnapshot
from app.contracts.session import (
    BookmarkCreated,
    ClientMessage,
    Control,
    Interrupt,
    Playback,
    ServerMessage,
    StateMsg,
    TranscriptMsg,
    Utterance,
)

Responder = Callable[[ChatRequest], "dict[str, Any] | str | BaseModel | ChatResponse"]


class FakeModelClient:
    """Routes each request to `responders[role]` (or `default`). Records every request."""

    def __init__(self, responders: dict[str, Responder] | None = None, default: Responder | None = None):
        self.responders = responders or {}
        self.default = default
        self.requests: list[ChatRequest] = []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        fn = self.responders.get(req.role, self.default)
        if fn is None:
            raise KeyError(f"FakeModelClient: no responder for role {req.role!r}")
        out = fn(req)
        if isinstance(out, ChatResponse):
            return out
        if isinstance(out, BaseModel):
            out = out.model_dump()
        if isinstance(out, dict):
            return ChatResponse(
                model="fake", provider="fake", parsed=out, usage=Usage(tokens_in=10, tokens_out=5)
            )
        return ChatResponse(
            model="fake", provider="fake", content=str(out), usage=Usage(tokens_in=10, tokens_out=5)
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:  # pragma: no cover - unused
        resp = await self.chat(req)
        yield StreamEvent(type="done", response=resp)

    def by_role(self, role: str) -> list[ChatRequest]:
        return [r for r in self.requests if r.role == role]


def scripted_sim_client(seed: int = 0) -> FakeModelClient:
    """A deterministic stand-in for `sim.user` / `sim.rate`: interrupts with a question about one
    story in three (by hash of the prompt), otherwise lets the brief play; rates every story 3."""
    import re

    def user(req: ChatRequest) -> dict[str, Any]:
        prompt = req.messages[-1].content
        m = re.search(r"Allowed actions now: (.*)", prompt)
        allowed = [a.strip() for a in m.group(1).split(",")] if m else ["let_play"]
        h = _h(seed, prompt) % 3
        for a in ("interrupt", "ask") if h == 0 else ("let_play", "end"):
            if a in allowed:
                return {
                    "action": a,
                    "text": "What exactly was announced?" if a in ("interrupt", "ask") else "",
                }
        return {"action": allowed[-1], "text": ""}

    def rate(req: ChatRequest) -> dict[str, Any]:
        refs = re.findall(r"^(\S+): ", req.messages[-1].content.split("STORIES", 1)[-1], re.M)
        return {"ratings": [{"story_ref": r, "rating": 3} for r in refs]}

    return FakeModelClient({"sim.user": user, "sim.rate": rate})


# ---- fake voice agent (server side of the text session protocol) ------------------------------


class FakeVoiceAgent:
    """Answers each utterance with one canned sentence. Questions containing 'unknowable' are
    bookmarked. Supports the client→server messages the simulator sends."""

    def __init__(
        self,
        brief: BriefScript,
        answer: str = "Here is what the sources say [p:p1].",
        bookmark_trigger: str = "unknowable",
    ):
        self.brief = brief
        self.answer = answer
        self.bookmark_trigger = bookmark_trigger
        self.received: list[ClientMessage] = []
        self.turn_log: list[dict[str, Any]] = []
        self._out: asyncio.Queue[ServerMessage] = asyncio.Queue()
        self._n = 0
        self._interrupted = False

    async def send(self, msg: ClientMessage) -> None:
        self.received.append(msg)
        if isinstance(msg, Interrupt):
            self._interrupted = True
            await self._out.put(StateMsg(state="LISTENING"))
        elif isinstance(msg, Utterance):
            self._n += 1
            tid = f"t{self._n}"
            await self._out.put(StateMsg(state="THINKING"))
            await self._out.put(TranscriptMsg(role="user", text=msg.text, turn_id=tid + "u"))
            tools: list[str] = []
            if self.bookmark_trigger in msg.text.lower():
                tools.append("bookmark_question")
                await self._out.put(BookmarkCreated(question_id=f"q{self._n}"))
                text = "I don't have that yet; I've saved the question and will follow up."
                cites: list[str] = []
            else:
                text, cites = self.answer, ["p1"]
            self.turn_log.append({"turn_id": tid, "tools": tools, "ttft_ms": 400})
            await self._out.put(StateMsg(state="SPEAKING_ANSWER"))
            await self._out.put(TranscriptMsg(role="agent", text=text, turn_id=tid, citations=cites))
            await self._out.put(StateMsg(state="OPEN_FLOOR" if not self._interrupted else "LISTENING"))
        elif isinstance(msg, Control):
            if msg.action == "resume":
                self._interrupted = False
                await self._out.put(StateMsg(state="PLAYING_BRIEF"))
            elif msg.action == "end":
                await self._out.put(StateMsg(state="ENDED"))
        elif isinstance(msg, Playback):
            pass

    async def recv(self, timeout_s: float | None = 1.0) -> ServerMessage | None:
        try:
            return await asyncio.wait_for(self._out.get(), timeout=timeout_s)
        except TimeoutError:
            return None


# ---- tiny synthetic world for runner tests ----------------------------------------------------

_FAKE_STORIES = [
    ("open-models", "Fakeco", "Fakeco releases open-weights model FK-1"),
    ("chips-and-compute", "Chipcorp", "Chipcorp ships accelerator CX-9"),
    ("policy-and-regulation", "Agency", "Agency publishes draft AI rule 12"),
    ("agents-and-tools", "Toolsmith", "Toolsmith launches coding agent TS"),
    ("evals-and-safety", "Evalab", "Evalab reports new jailbreak benchmark"),
    ("business-and-funding", "Startco", "Startco raises $40M Series B"),
    ("research-methods", "Unilab", "Unilab paper on long-context training"),
    ("frontier-models", "Biglab", "Biglab launches model BL-5"),
]


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def fake_digest(day: date, beat: str = "ai", run_id: str = "fake") -> Digest:
    entries = []
    rng = random.Random(_h(day, beat))
    for i, (sub, ent, title) in enumerate(_FAKE_STORIES):
        sid = f"st-{day.isoformat()}-{i}"
        entries.append(
            DigestEntry(
                story_id=sid,
                headline=f"[synthetic] {title}",
                summary="Synthetic test story.",
                subtopics=[sub],
                entities=[ent],
                importance=rng.randint(20, 95),
                evidence=["synthetic"],
                features=Features(n_items=1),
                urls=[f"https://example.invalid/{sid}"],
            )
        )
    at = datetime(day.year, day.month, day.day, 6, tzinfo=UTC)
    return Digest(
        digest_id=f"dg-{day.isoformat()}", run_id=run_id, beat=beat, at=at, version="fake", entries=entries
    )


class FakeAgent:
    """Implements AgentEntryPoints over synthetic digests. Deterministic given its inputs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._agents: dict[str, FakeVoiceAgent] = {}
        self._snap: dict[str, list[Interest]] = {}

    async def newsroom_run(self, beat: str, at: datetime, ctx: Any) -> Digest:
        self.calls.append(("newsroom_run", at.date().isoformat()))
        return fake_digest(at.date(), beat, run_id=ctx.digests_run_id)

    async def create_user(self, profile_id: str, onboarding_text: str, ctx: Any) -> str:
        self.calls.append(("create_user", profile_id))
        return f"u-{profile_id}"

    async def cos_daily(self, user_id: str, local_date: date, digests: list[Digest], ctx: Any) -> Any:
        from eval.runner.interfaces import DailyBrief

        self.calls.append(("cos_daily", f"{user_id}:{local_date}"))
        entries = sorted(digests[0].entries, key=lambda e: (-e.importance, e.story_id))
        rng = random.Random(_h(user_id, local_date, ctx.seed))
        picked = entries[:2] + rng.sample(entries[2:], 2)
        stories, sentences, packs = [], [BriefSentence(sid="s0", text="Good morning.", kind="intro")], []
        for i, e in enumerate(picked):
            slot = "big_news" if i < 2 else "for_you"
            stories.append(
                BriefStory(
                    story_ref=e.story_id,
                    slot=slot,
                    headline=e.headline,
                    why_tag="big news" if i < 2 else "for you",
                    aliases=e.entities,
                )
            )
            sentences.append(
                BriefSentence(
                    sid=f"s{i + 1}a",
                    text=e.headline + ".",
                    story_ref=e.story_id,
                    cites=[f"p-{e.story_id}"],
                    kind="story",
                )
            )
            sentences.append(
                BriefSentence(
                    sid=f"s{i + 1}b",
                    text=f"More detail about {e.entities[0]} follows.",
                    story_ref=e.story_id,
                    cites=[f"p-{e.story_id}"],
                    kind="story",
                )
            )
            packs.append(
                SourcePack(
                    story_ref=e.story_id,
                    headline=e.headline,
                    facts=[],
                    depth="standard",
                    passages=[
                        Passage(
                            passage_id=f"p-{e.story_id}",
                            doc_id="d",
                            url=e.urls[0],
                            text=e.summary,
                            char_start=0,
                            char_end=len(e.summary),
                        )
                    ],
                )
            )
        sentences.append(BriefSentence(sid="s9", text="That's the brief.", kind="outro"))
        brief = BriefScript(
            brief_id=f"b-{user_id}-{local_date}",
            user_id=user_id,
            local_date=local_date,
            arm=ctx.experiment.id,
            stories=stories,
            sentences=sentences,
            est_duration_s=60,
        )
        return DailyBrief(brief=brief, packs=packs, candidates=[])

    async def open_session(self, user_id: str, daily: Any, ctx: Any) -> tuple[str, FakeVoiceAgent]:
        sid = f"sess-{daily.brief.brief_id}"
        agent = FakeVoiceAgent(daily.brief)
        self._agents[sid] = agent
        return sid, agent

    async def session_turns(self, session_id: str, ctx: Any) -> list[dict[str, Any]]:
        return list(self._agents[session_id].turn_log)

    async def memory_update(self, user_id: str, session_id: str, ctx: Any) -> MemorySnapshot:
        self.calls.append(("memory_update", session_id))
        ints = self._snap.setdefault(
            user_id,
            [
                Interest(
                    id="i1",
                    label="open models",
                    subtopic="open-models",
                    weight=0.5,
                    confidence=0.3,
                    source="onboarding",
                ),
                Interest(
                    id="i2",
                    label="chips",
                    subtopic="chips-and-compute",
                    weight=0.5,
                    confidence=0.3,
                    source="onboarding",
                ),
            ],
        )
        return MemorySnapshot(
            user_id=user_id, run_id=ctx.run_id, as_of=ctx.clock.now().date(), interests=ints, delivery=[]
        )

    async def followup_research(self, user_id: str, ctx: Any) -> list[dict[str, Any]]:
        self.calls.append(("followup_research", user_id))
        return []
