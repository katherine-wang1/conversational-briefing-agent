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

from app.contracts.agent_io import (
    MemoryUpdateReport,
    OpenQuestion,
    SessionOutcome,
    SessionRecord,
    ShownStory,
    UserContext,
)
from app.contracts.brief import BriefScript, BriefSentence, BriefStory, Fact, Passage, SourcePack
from app.contracts.digest import Digest, DigestEntry, StoryFeatures
from app.contracts.llm import ModelClient
from app.contracts.memory import Interest, MemorySnapshot
from app.contracts.sources import HeadlineObservation

log = logging.getLogger(__name__)


class UserCtx(dict):
    """user_id, run_id, local_date, now, timezone, brief_id, arm, job_id, email-free profile settings.
    `RealAgents` turns it into `app.contracts.agent_io.UserContext`."""


class AgentPorts(Protocol):
    """What the job handlers call. Inputs are the typed shapes from app/contracts/agent_io.py, built
    from the DB by app/jobs/agent_inputs.py."""

    name: str

    async def run_newsroom(
        self,
        beat_id: str,
        at: datetime,
        observations: list[HeadlineObservation],
        model_client: ModelClient | None,
        tools: dict[str, Any],
        flags: Any,
        **kw: Any,  # history: list[Story], previous_run_at, job_id (newsroom keyword args)
    ) -> tuple[Digest, Any]: ...

    async def run_cos_daily(
        self,
        user_ctx: UserCtx,
        digests: list[Digest],
        memory: MemorySnapshot,
        history: list[ShownStory],
        open_questions: list[OpenQuestion],
        flags: Any,
        model_client: ModelClient | None,
        tools: dict[str, Any],
    ) -> tuple[BriefScript, list[SourcePack], Any]: ...

    async def memory_update(
        self,
        memory: MemorySnapshot,
        session: SessionRecord,
        flags: Any,
        model_client: ModelClient | None,
        *,
        prior_sessions: list[SessionOutcome] = (),  # type: ignore[assignment]
        now: datetime | None = None,
        job_id: str | None = None,
    ) -> tuple[MemorySnapshot, MemoryUpdateReport | None]: ...

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

    async def run_newsroom(self, beat_id, at, observations, model_client, tools, flags, **kw):  # noqa: ANN001,ANN003
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

    async def memory_update(self, memory, session, flags, model_client, **kw):  # noqa: ANN001,ANN003
        return memory, None  # no learning in the placeholder

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


def _experiment(arm: str, flags: Any) -> Any:
    """The full experiment config for `arm` (keeps its selection_overrides); `flags` if unknown."""
    from app.config import loader

    try:
        return loader.experiment(arm)
    except FileNotFoundError:
        return flags


def user_context(user_ctx: UserCtx) -> UserContext:
    settings = user_ctx.get("settings") or {}
    now = user_ctx.get("now") or datetime.now(UTC)
    return UserContext(
        user_id=str(user_ctx["user_id"]),
        local_date=user_ctx["local_date"],
        now=now,
        mode=user_ctx.get("mode", "live"),
        run_id=user_ctx.get("run_id", "live"),
        job_id=str(user_ctx["job_id"]) if user_ctx.get("job_id") is not None else None,
        followed_beats=list(settings.get("followed_beats") or []),
        arm=user_ctx.get("arm", "full"),
    )


class RealAgents:
    """Adapter from the job handlers' calls to the agent workstreams' entry points (W-C newsroom,
    W-D chief of staff / memory / follow-up). A model client is required: real agents never run
    without one (the handlers fail the job instead of producing placeholder output)."""

    name = "real"

    def __init__(self) -> None:
        self.newsroom = importlib.import_module("app.agents.newsroom")
        self.cos = importlib.import_module("app.agents.cos")
        self.memory = importlib.import_module("app.agents.memory")
        self.followup = importlib.import_module("app.agents.followup")
        for mod, attr in (
            (self.newsroom, "run_newsroom"),
            (self.cos, "run_cos_daily"),
            (self.memory, "update_memory_after_session"),
            (self.memory, "extract_onboarding"),
            (self.followup, "run_followup"),
        ):
            if not hasattr(mod, attr):
                raise ImportError(f"{mod.__name__}.{attr} missing")

    @staticmethod
    def _need(model_client: ModelClient | None) -> ModelClient:
        if model_client is None:
            raise RuntimeError("no model client configured (OPENROUTER_API_KEY); real agents need one")
        return model_client

    async def run_newsroom(self, beat_id, at, observations, model_client, tools, flags, **kw):  # noqa: ANN001,ANN003
        return await self.newsroom.run_newsroom(
            beat_id, at, observations, self._need(model_client), tools, flags, **kw
        )

    async def run_cos_daily(
        self, user_ctx, digests, memory, history, open_questions, flags, model_client, tools
    ):  # noqa: ANN001
        uc = user_context(user_ctx)
        script, packs, trace = await self.cos.run_cos_daily(
            uc,
            digests,
            memory,
            history,
            open_questions,
            _experiment(uc.arm, flags),
            self._need(model_client),
            tools,
        )
        return script, packs, trace

    async def memory_update(
        self, memory, session, flags, model_client, *, prior_sessions=(), now=None, job_id=None
    ):  # noqa: ANN001
        new, report, _proposal, _trace = await self.memory.update_memory_after_session(
            memory,
            session,
            model_client=self._need(model_client),
            flags=flags,
            prior_sessions=list(prior_sessions),
            now=now or datetime.now(UTC),
            run_id=memory.run_id,
            job_id=job_id,
        )
        return new, report

    async def extract_onboarding(self, user_id, text, model_client):  # noqa: ANN001
        snap, _trace = await self.memory.extract_onboarding(
            text, user_id=str(user_id), now=datetime.now(UTC), model_client=self._need(model_client)
        )
        return list(snap.interests)

    async def run_followup(self, question, model_client, tools):  # noqa: ANN001
        q = OpenQuestion(
            id=question["id"],
            question=question["question"],
            context={
                **(question.get("context") or {}),
                **({"story_ref": question["story_ref"]} if question.get("story_ref") else {}),
            },
            status="researching",
            created_at=question["created_at"],
        )
        now = datetime.now(UTC)
        uc = UserContext(
            user_id=str(question["user_id"]),
            local_date=now.date(),
            now=now,
            run_id=question.get("run_id", "live"),
            job_id=str(question["job_id"]) if question.get("job_id") is not None else None,
        )
        answer, _trace = await self.followup.run_followup(
            q, user_ctx=uc, model_client=self._need(model_client), tools=tools
        )
        return answer.model_dump(mode="json")


def load_ports(mode: str = "auto", *, model_available: bool = True) -> AgentPorts:
    """`real`: the agents (fail loudly if missing). `stub`: the placeholder. `auto`: the agents when
    their code is present and a model client is configured, else the placeholder (with a warning)."""
    if mode == "stub":
        return StubAgents()
    try:
        ports = RealAgents()
    except (ImportError, AttributeError) as e:
        if mode == "real":
            raise
        log.warning("agent entry points unavailable (%s); using StubAgents placeholder", e)
        return StubAgents()
    if mode == "auto" and not model_available:
        bar = "!" * 78
        log.error(
            "\n%s\n!! NO MODEL KEY: OPENROUTER_API_KEY is empty, so this worker runs PLACEHOLDER agents.\n"
            "!! Briefs will be fake (the digest headlines read back, no search, no real writing).\n"
            "!! Fix: set OPENROUTER_API_KEY in .env (or export it); make sure this shell doesn't export an\n"
            "!! empty one (`unset OPENROUTER_API_KEY`); restart; then reset the database (README).\n%s",
            bar,
            bar,
        )
        return StubAgents()
    return ports


def load_model_client(db: Any = None) -> ModelClient | None:
    """W-B's Router via `app.llm.get_client(db)` (ledger to JSONL + `llm_calls` when `db` is given);
    None when the model layer or its key is missing (stub ports need no model)."""
    try:
        llm = importlib.import_module("app.llm")
    except ImportError:
        return None
    fn = getattr(llm, "get_client", None)
    return fn(db) if callable(fn) else None
