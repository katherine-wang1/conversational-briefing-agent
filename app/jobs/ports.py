"""Agent entry points the job handlers call (W-C newsroom, W-D chief of staff / memory / follow-up).

Handlers depend on the `AgentPorts` protocol only. `load_ports()` returns the real agents when their
modules exist (after W-C / W-D merge) and `StubAgents` otherwise.

`StubAgents` is a **development placeholder**, not an agent: it makes no model calls, its briefs are
marked `arm="stub"` and read the digest headlines back verbatim, and follow-ups are always
"unanswerable". It exists so the platform (queue → brief → audio → session) runs end to end before
the agent workstreams land. Never use it for evaluation.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

from app.contracts.brief import BriefScript, BriefSentence, BriefStory, Fact, Passage, SourcePack
from app.contracts.digest import Digest, DigestEntry, StoryFeatures
from app.contracts.llm import ModelClient
from app.contracts.memory import Interest, MemorySignal, MemorySnapshot, SessionContext
from app.contracts.sources import HeadlineObservation

log = logging.getLogger(__name__)


class UserCtx(dict):
    """user_id, run_id, local_date, timezone, email-free profile settings, arm. A dict on purpose:
    the chief of staff defines what it reads."""


class AgentPorts(Protocol):
    name: str

    async def run_newsroom(
        self,
        beat_id: str,
        at: datetime,
        observations: list[HeadlineObservation],
        model_client: ModelClient | None,
        tools: dict[str, Any],
        flags: Any,
    ) -> tuple[Digest, Any]: ...

    async def run_cos_daily(
        self,
        user_ctx: UserCtx,
        digests: list[Digest],
        memory: MemorySnapshot,
        history: list[dict[str, Any]],
        open_questions: list[dict[str, Any]],
        flags: Any,
        model_client: ModelClient | None,
        tools: dict[str, Any],
    ) -> tuple[BriefScript, list[SourcePack], Any]: ...

    async def memory_update(
        self,
        memory: MemorySnapshot,
        transcript: list[dict[str, Any]],
        signals: list[MemorySignal],
        shown: list[str],
        session_ctx: SessionContext,
        flags: Any,
        model_client: ModelClient | None,
    ) -> MemorySnapshot: ...

    async def extract_onboarding(
        self, user_id: UUID, text: str, model_client: ModelClient | None
    ) -> list[Interest]: ...

    async def run_followup(
        self,
        question: dict[str, Any],
        model_client: ModelClient | None,
        tools: dict[str, Any],
    ) -> dict[str, Any]: ...


class StubAgents:
    """Deterministic placeholder (see module docstring)."""

    name = "stub"

    async def run_newsroom(self, beat_id, at, observations, model_client, tools, flags):  # noqa: ANN001
        seen: dict[str, HeadlineObservation] = {}
        for o in sorted(observations, key=lambda o: (o.rank or 999, o.observed_at)):
            seen.setdefault(o.url, o)
        entries = []
        for i, o in enumerate(list(seen.values())[:30]):
            sid = f"stub-{beat_id}-{hashlib.sha256(o.url.encode()).hexdigest()[:12]}"
            entries.append(
                DigestEntry(
                    story_id=sid,
                    headline=o.title,
                    summary=o.snippet or "",
                    subtopics=[],
                    entities=[],
                    importance=max(0, 100 - 3 * i),
                    evidence=[f"{o.source_id} rank {o.rank}"],
                    features=StoryFeatures(n_items=1, first_seen=o.observed_at),
                    urls=[o.url],
                )
            )
        digest = Digest(
            digest_id=f"dg-stub-{beat_id}-{at:%Y%m%d%H}", beat=beat_id, at=at, version="stub", entries=entries
        )
        return digest, {"stub": True}

    async def run_cos_daily(
        self, user_ctx, digests, memory, history, open_questions, flags, model_client, tools
    ):  # noqa: ANN001,E501
        entries = sorted((e for d in digests for e in d.entries), key=lambda e: -e.importance)[:4]
        local_date: date = user_ctx["local_date"]
        brief_id = user_ctx.get("brief_id") or f"br-stub-{user_ctx['user_id']}-{local_date}"
        stories, sentences, packs = [], [], []
        sentences.append(
            BriefSentence(sid="s0", text="Good morning. This is a placeholder brief.", kind="intro")
        )
        for i, e in enumerate(entries):
            pid = f"p-{e.story_id}"
            stories.append(
                BriefStory(
                    story_ref=e.story_id,
                    slot="big_news",
                    headline=e.headline,
                    why_tag="big news",
                    aliases=e.entities,
                )
            )
            text = e.summary or e.headline
            sentences.append(
                BriefSentence(
                    sid=f"s{i + 1}", text=f"{e.headline}.", story_ref=e.story_id, cites=[pid], kind="story"
                )
            )
            packs.append(
                SourcePack(
                    story_ref=e.story_id,
                    headline=e.headline,
                    facts=[Fact(text=text, passage_ids=[pid])],
                    passages=[
                        Passage(
                            passage_id=pid,
                            doc_id=e.story_id,
                            url=(e.urls or [""])[0],
                            text=text,
                            char_start=0,
                            char_end=len(text),
                        )
                    ],
                    depth="light",
                )
            )
        sentences.append(BriefSentence(sid=f"s{len(entries) + 1}", text="That's the brief.", kind="outro"))
        script = BriefScript(
            brief_id=brief_id,
            user_id=str(user_ctx["user_id"]),
            local_date=local_date,
            arm="stub",
            stories=stories,
            sentences=sentences,
            est_duration_s=20 * len(sentences),
        )
        return script, packs, {"stub": True}

    async def memory_update(self, memory, transcript, signals, shown, session_ctx, flags, model_client):  # noqa: ANN001,E501
        return memory  # no learning in the placeholder

    async def extract_onboarding(self, user_id, text, model_client):  # noqa: ANN001
        out = []
        for i, phrase in enumerate(p.strip() for p in text.replace("\n", ",").split(",")):
            if phrase:
                out.append(
                    Interest(
                        id=f"int-{user_id.hex[:8]}-{i}",
                        label=phrase[:200],
                        subtopic="other",
                        weight=0.4,
                        confidence=0.3,
                        source="onboarding",
                        last_reinforced_at=datetime.now(UTC),
                    )
                )
        return out

    async def run_followup(self, question, model_client, tools):  # noqa: ANN001
        return {
            "status": "unanswerable",
            "answer_sentences": [],
            "what_was_found": "Follow-up research is not wired up yet (placeholder).",
        }


class RealAgents:
    """Adapter over the agent workstreams' entry points. Signatures are reconciled at merge time."""

    name = "real"

    def __init__(self) -> None:
        self.newsroom = importlib.import_module("app.agents.newsroom")
        self.cos = importlib.import_module("app.agents.cos")
        self.memory = importlib.import_module("app.agents.memory")
        self.followup = importlib.import_module("app.agents.followup")
        for mod, attr in ((self.newsroom, "run_newsroom"), (self.cos, "run_cos_daily")):
            if not hasattr(mod, attr):
                raise ImportError(f"{mod.__name__}.{attr} missing")

    async def run_newsroom(self, *a, **kw):  # noqa: ANN002,ANN003
        return await self.newsroom.run_newsroom(*a, **kw)

    async def run_cos_daily(self, *a, **kw):  # noqa: ANN002,ANN003
        return await self.cos.run_cos_daily(*a, **kw)

    async def memory_update(self, *a, **kw):  # noqa: ANN002,ANN003
        return await self.memory.apply_memory_update(*a, **kw)

    async def extract_onboarding(self, *a, **kw):  # noqa: ANN002,ANN003
        return await self.memory.extract_onboarding(*a, **kw)

    async def run_followup(self, *a, **kw):  # noqa: ANN002,ANN003
        return await self.followup.run_followup(*a, **kw)


def load_ports(mode: str = "auto") -> AgentPorts:
    if mode == "stub":
        return StubAgents()
    try:
        return RealAgents()
    except (ImportError, AttributeError) as e:
        if mode == "real":
            raise
        log.warning("agent entry points unavailable (%s); using StubAgents placeholder", e)
        return StubAgents()


def load_model_client() -> ModelClient | None:
    """The W-B router if present (app.llm.get_client / Router), else None (stubs need no model)."""
    try:
        llm = importlib.import_module("app.llm")
    except ImportError:
        return None
    for attr in ("get_client", "default_client", "build_client"):
        fn = getattr(llm, attr, None)
        if callable(fn):
            return fn()
    return None
