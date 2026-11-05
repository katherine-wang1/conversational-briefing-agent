"""Simulated user (TDD §17.2, PRD SIM-1..4).

Per-turn loop over the text session protocol (`app/contracts/session.py`). The simulator plays the
brief itself (text mode, no TTS): each sentence costs `words / speaking_wps` seconds of the user's
sampled time budget. Code (not the model) governs time, the number of questions, the share of
unanswerable questions, explicitness and noise; the model (`sim.user`) only picks an action from the
allowed set and words it. Ratings are a separate `sim.rate` call.

The simulator never sees agent internals (scores, memory, labels): only brief sentences it heard and
the agent's spoken replies.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.config import loader
from app.contracts.brief import BriefScript, BriefSentence
from app.contracts.common import TraceCtx
from app.contracts.llm import ModelClient
from app.contracts.session import (
    BookmarkCreated,
    ClientHello,
    ClientMessage,
    Control,
    Interrupt,
    Playback,
    ResumeBrief,
    ServerMessage,
    StateMsg,
    TranscriptMsg,
    Utterance,
)
from eval.runner.modelcall import Prompt, call_structured, load_prompt
from eval.sim.profile import SimProfile

PROMPTS = Path(__file__).resolve().parent / "prompts"

ActionName = Literal["interrupt", "ask", "say_pref", "save", "skip", "end", "let_play"]
QUESTION_ACTIONS = {"interrupt", "ask"}
SPEAKING_ACTIONS = {"interrupt", "ask", "say_pref", "save"}
TERMINAL_STATES = {"OPEN_FLOOR", "LISTENING", "PLAYING_BRIEF", "ENDED", "READY"}


class SessionTransport(Protocol):
    """Client side of the session protocol in text mode."""

    async def send(self, msg: ClientMessage) -> None: ...

    async def recv(self, timeout_s: float | None = None) -> ServerMessage | None: ...


# ---- structured outputs ------------------------------------------------------------------------


class SimAction(BaseModel):
    """`sim.user` structured output (TDD §17.2)."""

    action: ActionName
    text: str = ""
    target_story_id: str | None = None  # for logging only
    at_segment: str | None = None
    polarity: Literal["more", "less"] | None = None


class StoryRating(BaseModel):
    story_ref: str
    rating: int = Field(ge=1, le=5)


class SimRatings(BaseModel):
    """`sim.rate` structured output."""

    ratings: list[StoryRating]


# ---- session log -------------------------------------------------------------------------------


class SimTurn(BaseModel):
    t_s: float  # session clock (seconds since start)
    actor: Literal["user", "agent"]
    action: str
    text: str = ""
    target_story_id: str | None = None
    at_segment: str | None = None
    citations: list[str] = []


class QuestionRecord(BaseModel):
    qid: str
    text: str
    target_story_id: str | None
    at_segment: str | None
    intended_unanswerable: bool
    phase: Literal["brief", "followup", "open_floor"]
    answer_text: str = ""
    citations: list[str] = []
    bookmarked: bool = False
    bookmark_id: str | None = None


class StoryOutcome(BaseModel):
    story_ref: str
    heard: Literal["full", "partial", "none"] = "none"
    engaged: bool = False  # asked about it, saved it, or said "more like this"
    skipped: bool = False
    saved: bool = False
    pref: Literal["more", "less"] | None = None


class SessionLog(BaseModel):
    session_id: str
    profile_id: str
    day: int
    seed: int
    time_budget_s: float
    time_used_s: float = 0.0
    end_reason: Literal["natural", "cut_short", "idle", "error"] = "natural"
    brief_completed: bool = False
    max_questions: int = 0
    turns: list[SimTurn] = []
    questions: list[QuestionRecord] = []
    stories: list[StoryOutcome] = []
    latent_leaks_blocked: int = 0
    invalid_actions: int = 0
    model_calls: int = 0
    prompt_sha: str = ""


class AgentReply(BaseModel):
    text: str = ""
    citations: list[str] = []
    bookmark_id: str | None = None
    resumed: bool = False
    ended: bool = False


# ---- helpers -----------------------------------------------------------------------------------


def stable_seed(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def words(text: str) -> int:
    return max(1, len(text.split()))


def _desc(subtopic: str) -> str:
    for s in loader.taxonomy().subtopics:
        if s.id == subtopic:
            return s.description
    return subtopic


def _level(w: float) -> str:
    return "top priority" if w >= 0.35 else "important" if w >= 0.2 else "somewhat interested"


def render_interests(profile: SimProfile, day: int) -> str:
    lines = []
    for i in sorted(profile.interests_at(day), key=lambda x: -x.weight):
        extra = ", ".join([*i.entities, *i.keywords])
        lines.append(f"- {_desc(i.subtopic)} ({_level(i.weight)})" + (f"; especially: {extra}" if extra else ""))
    return "\n".join(lines)


def render_latent(profile: SimProfile) -> str:
    if not profile.latent_interests:
        return "(none)"
    lines = []
    for li in profile.latent_interests:
        head = _desc(li.subtopic) if li.subtopic and not li.terms else ""
        lines.append("- " + ", ".join(t for t in [head, *li.terms] if t))
    return "\n".join(lines)


def render_delivery(profile: SimProfile) -> str:
    d = profile.delivery
    return (f"About {d.length_s} seconds of brief; {d.depth} depth; {d.technicality} technicality; "
            f"prefers {d.style}.")


def latent_leak(text: str, profile: SimProfile, heard_lower: str) -> list[str]:
    """Latent terms said by the user that the agent has not mentioned first."""
    t = text.lower()
    return [term for li in profile.latent_interests for term in li.terms
            if term.lower() in t and term.lower() not in heard_lower]


# ---- simulator ---------------------------------------------------------------------------------


class Simulator:
    def __init__(
        self,
        profile: SimProfile,
        client: ModelClient,
        *,
        day: int,
        seed: int,
        run_id: str = "sim",
        cache_namespace: str = "sim",
        user_prompt: Prompt | None = None,
        rate_prompt: Prompt | None = None,
    ):
        self.p = profile
        self.client = client
        self.day = day
        self.seed = seed
        self.run_id = run_id
        self.cache_namespace = cache_namespace
        self.rng = random.Random(stable_seed(seed, profile.id, day))
        self.user_prompt = user_prompt or load_prompt(PROMPTS / "sim_user.md")
        self.rate_prompt = rate_prompt or load_prompt(PROMPTS / "sim_rate.md")

    # -- sampling (code-governed behaviour) --

    def sample_time_budget(self) -> float:
        tb = self.p.behavior.time_budget_s
        if tb.dist == "fixed" and tb.fixed_s is not None:
            return float(tb.fixed_s)
        if tb.dist == "uniform":
            lo, hi = min(tb.rushed[0], tb.relaxed[0]), max(tb.rushed[1], tb.relaxed[1])
            return self.rng.uniform(lo, hi)
        rng_range = tb.rushed if self.rng.random() < tb.rushed_p else tb.relaxed
        return self.rng.uniform(*rng_range)

    def sample_max_questions(self) -> int:
        b = self.p.behavior
        return sum(1 for _ in range(b.max_questions) if self.rng.random() < b.followup_rate)

    def _dur(self, text: str) -> float:
        return words(text) / self.p.behavior.speaking_wps

    # -- main loop --

    async def run_session(self, brief: BriefScript, transport: SessionTransport, session_id: str) -> SessionLog:
        log = SessionLog(session_id=session_id, profile_id=self.p.id, day=self.day, seed=self.seed,
                         time_budget_s=round(self.sample_time_budget(), 1), prompt_sha=self.user_prompt.sha)
        log.max_questions = self.sample_max_questions()
        self._log = log
        self._clock = 0.0
        self._heard: list[str] = []
        self._conv: list[str] = []
        self._qn = 0
        self._pending_unanswerable = False
        outcomes = {s.story_ref: StoryOutcome(story_ref=s.story_ref) for s in brief.stories}
        log.stories = list(outcomes.values())
        self._story_index = {s.story_ref: s for s in brief.stories}
        self._first_sentence: dict[str, str] = {}

        await transport.send(ClientHello(session_id=session_id, client={"mode": "text", "stt": "sim"}))
        await transport.send(Control(action="play"))

        segments = _group(brief.sentences)
        ended = False
        for story_ref, sents in segments:
            if ended:
                break
            if story_ref is None:  # intro / transition / outro
                for s in sents:
                    if not await self._play(transport, s, None, outcomes):
                        ended = True
                        break
                continue
            out = outcomes.get(story_ref) or StoryOutcome(story_ref=story_ref)
            k = self.rng.randint(1, len(sents))  # decision point: after sentence k
            i = 0
            while i < k:
                if not await self._play(transport, sents[i], story_ref, outcomes):
                    ended = True
                    break
                i += 1
            if ended:
                break
            out.heard = "full" if k == len(sents) else "partial"
            act = await self._decide(phase="brief", story_ref=story_ref, segment=sents[k - 1].sid,
                                     allowed=self._allowed_brief())
            if act.action == "end":
                await transport.send(Control(action="end"))
                ended = True
                log.end_reason = "cut_short"
                break
            if act.action == "skip":
                out.skipped = True
                await transport.send(Control(action="skip"))
                continue
            resume_from = k
            if act.action in SPEAKING_ACTIONS:
                await transport.send(Interrupt(segment_id=sents[k - 1].sid,
                                               offset_ms=int(self._dur(sents[k - 1].text) * 1000)))
                cont = await self._speak(transport, act, story_ref, sents[k - 1].sid, "brief", out)
                if cont == "end":
                    ended = True
                    log.end_reason = "cut_short"
                    break
                resume_from = k - 1  # resume from the start of the interrupted sentence (TDD §12.1)
                await transport.send(Control(action="resume"))
            for s in sents[resume_from:]:
                if not await self._play(transport, s, story_ref, outcomes):
                    ended = True
                    break
            if not ended:
                out.heard = "full"

        if not ended:
            log.brief_completed = True
            # open floor
            for _ in range(4):
                if self._remaining() <= 0:
                    break
                allowed = [a for a in ("ask", "say_pref", "end") if self._allow(a)]
                act = await self._decide(phase="open_floor", story_ref=None, segment=None, allowed=allowed)
                if act.action not in SPEAKING_ACTIONS:
                    break
                tgt = act.target_story_id if act.target_story_id in outcomes else None
                cont = await self._speak(transport, act, tgt, None, "open_floor",
                                         outcomes.get(tgt) if tgt else None)
                if cont == "end":
                    break
            await transport.send(Control(action="end"))
            log.end_reason = "natural"
        else:
            log.end_reason = "cut_short"
        log.time_used_s = round(min(self._clock, log.time_budget_s), 1)
        return log

    # -- pieces --

    def _remaining(self) -> float:
        return self._log.time_budget_s - self._clock

    def _allow(self, action: str) -> bool:
        if action in QUESTION_ACTIONS:
            return sum(1 for _ in self._log.questions) < self._log.max_questions
        return True

    def _allowed_brief(self) -> list[str]:
        return [a for a in ("let_play", "interrupt", "say_pref", "save", "skip", "end") if self._allow(a)]

    async def _play(self, transport: SessionTransport, s: BriefSentence, story_ref: str | None,
                    outcomes: dict[str, StoryOutcome]) -> bool:
        d = self._dur(s.text)
        if self._remaining() < d:
            self._log.end_reason = "cut_short"
            await transport.send(Control(action="end"))
            return False
        await transport.send(Playback(segment_id=s.sid, offset_ms=0))
        self._clock += d
        self._heard.append(s.text)
        if story_ref and story_ref not in self._first_sentence:
            self._first_sentence[story_ref] = s.text
        if story_ref and story_ref in outcomes and outcomes[story_ref].heard == "none":
            outcomes[story_ref].heard = "partial"
        return True

    async def _speak(self, transport: SessionTransport, act: SimAction, story_ref: str | None,
                     segment: str | None, phase: str, out: StoryOutcome | None) -> str:
        """Say something, get the reply, then run the follow-up loop. Returns 'resume' or 'end'."""
        current: SimAction | None = act
        cur_phase = phase
        while current is not None and current.action in SPEAKING_ACTIONS:
            await self._utter(transport, current, story_ref, segment, cur_phase, out)
            if self._remaining() <= 0:
                return "end"
            cur_phase = "followup" if phase != "open_floor" else "open_floor"
            allowed = [a for a in ("ask", "say_pref", "let_play", "end") if self._allow(a)]
            if phase == "open_floor":
                allowed = [a for a in allowed if a != "let_play"] + ["let_play"]
            current = await self._decide(phase="followup", story_ref=story_ref, segment=segment, allowed=allowed)
            if current.action == "end":
                return "end"
        return "resume"

    async def _utter(self, transport: SessionTransport, act: SimAction, story_ref: str | None,
                     segment: str | None, phase: str, out: StoryOutcome | None) -> None:
        text = act.text.strip() or {"save": "Save that one for later.", "say_pref": "More like this, please."}.get(
            act.action, "Can you tell me more about that?")
        self._clock += self.p.behavior.think_s + self._dur(text)
        self._log.turns.append(SimTurn(t_s=round(self._clock, 1), actor="user", action=act.action, text=text,
                                       target_story_id=story_ref, at_segment=segment))
        self._conv.append(f"YOU: {text}")
        q: QuestionRecord | None = None
        if act.action in QUESTION_ACTIONS:
            self._qn += 1
            q = QuestionRecord(qid=f"{self._log.session_id}-q{self._qn}", text=text, target_story_id=story_ref,
                               at_segment=segment, intended_unanswerable=self._pending_unanswerable,
                               phase=phase)  # type: ignore[arg-type]
            self._log.questions.append(q)
        if out is not None:
            if act.action in QUESTION_ACTIONS:
                out.engaged = True
            elif act.action == "save":
                out.saved = out.engaged = True
            elif act.action == "say_pref":
                out.pref = act.polarity or "more"
                out.engaged = out.engaged or out.pref == "more"
        await transport.send(Utterance(text=text, stt="sim"))
        reply = await self._exchange(transport)
        self._clock += self._dur(reply.text) if reply.text else 0.0
        self._log.turns.append(SimTurn(t_s=round(self._clock, 1), actor="agent", action="reply", text=reply.text,
                                       citations=reply.citations))
        self._conv.append(f"ASSISTANT: {reply.text}")
        self._heard.append(reply.text)
        if q is not None:
            q.answer_text = reply.text
            q.citations = reply.citations
            q.bookmarked = reply.bookmark_id is not None
            q.bookmark_id = reply.bookmark_id

    async def _exchange(self, transport: SessionTransport) -> AgentReply:
        reply = AgentReply()
        texts: list[str] = []
        for _ in range(200):
            msg = await transport.recv(timeout_s=30.0)
            if msg is None:
                break
            if isinstance(msg, TranscriptMsg) and msg.role == "agent":
                texts.append(msg.text)
                reply.citations += msg.citations
            elif isinstance(msg, BookmarkCreated):
                reply.bookmark_id = msg.question_id
            elif isinstance(msg, ResumeBrief):
                reply.resumed = True
                break
            elif isinstance(msg, StateMsg):
                if msg.state == "ENDED":
                    reply.ended = True
                    break
                if texts and msg.state in TERMINAL_STATES:
                    break
        reply.text = " ".join(texts)
        return reply

    async def _decide(self, *, phase: str, story_ref: str | None, segment: str | None,
                      allowed: list[str]) -> SimAction:
        if self._remaining() <= 0:
            return SimAction(action="end")
        b = self.p.behavior
        guidance = []
        explicit_now = self.rng.random() < b.explicitness
        if not explicit_now:
            allowed = [a for a in allowed if a != "say_pref"] or ["let_play"]
            guidance.append("Do not state preferences out loud right now; just act.")
        else:
            guidance.append("If you have a clear preference about this story, you may say it out loud (say_pref).")
        self._pending_unanswerable = any(a in QUESTION_ACTIONS for a in allowed) and \
            self.rng.random() < b.unanswerable_rate
        if self._pending_unanswerable:
            guidance.append("If you ask a question now, make it one the brief and its sources likely cannot "
                            "answer (a specific detail no article would contain).")
        if self.rng.random() < b.noise:
            guidance.append("This time, act against your usual preferences (for example, skip something you "
                            "would normally like, or ask about something you would normally ignore).")
        openness = ("curious and happy to dig into stories outside your usual interests"
                    if b.exploration_openness >= 0.7 else
                    "uninterested in stories outside your usual interests" if b.exploration_openness <= 0.2
                    else "occasionally interested in stories outside your usual interests")
        guidance.append(f"You are {openness}.")
        if phase == "brief":
            position = f"The brief is playing; you just heard part of story {story_ref}."
        elif phase == "followup":
            position = f"The assistant just answered you (about story {story_ref})."
        else:
            position = "The brief has finished; the assistant is waiting for anything else."

        stories = "\n".join(f"{ref}: {txt}" for ref, txt in self._first_sentence.items()) or "(none yet)"
        _, user = self.user_prompt.render(
            persona=self.p.persona, stated=self.p.onboarding, interests=render_interests(self.p, self.day),
            latent=render_latent(self.p), delivery=render_delivery(self.p), stories=stories,
            heard="\n".join(self._heard[-12:]) or "(nothing yet)",
            conversation="\n".join(self._conv[-8:]) or "(nothing yet)", position=position,
            remaining=str(max(0, int(self._remaining()))), guidance="\n".join(guidance),
            allowed=", ".join(allowed),
        )
        trace = TraceCtx(run_id=self.run_id, role="sim.user", step=f"{self.p.id}:d{self.day}:{phase}")
        heard_lower = " ".join(self._heard).lower()
        act: SimAction | None = None
        for attempt in range(2):
            extra = "" if attempt == 0 else ("\nIMPORTANT: you mentioned one of your private interests that the "
                                             "assistant has not brought up. Do not mention it.")
            self._log.model_calls += 1
            act, _ = await call_structured(
                self.client, role="sim.user", system=self.user_prompt.system, user=user + extra, schema=SimAction,
                trace=trace, cache_namespace=f"{self.cache_namespace}:{self.p.id}:{self.day}",
            )
            if act.action in SPEAKING_ACTIONS and latent_leak(act.text, self.p, heard_lower):
                self._log.latent_leaks_blocked += 1
                act = None
                continue
            break
        if act is None:
            act = SimAction(action="let_play" if "let_play" in allowed else "end")
        if act.action not in allowed:
            if act.action in QUESTION_ACTIONS and ({"interrupt", "ask"} & set(allowed)):
                act = act.model_copy(update={"action": next(a for a in ("interrupt", "ask") if a in allowed)})
            else:
                self._log.invalid_actions += 1
                act = SimAction(action="let_play" if "let_play" in allowed else "end")
        if story_ref is not None:
            act = act.model_copy(update={"target_story_id": story_ref, "at_segment": segment})
        return act

    # -- ratings --

    async def rate(self, brief: BriefScript) -> dict[str, int]:
        by_story: dict[str, list[str]] = {}
        for s in brief.sentences:
            if s.story_ref:
                by_story.setdefault(s.story_ref, []).append(s.text)
        stories = "\n".join(f"{st.story_ref}: {st.headline}. {' '.join(by_story.get(st.story_ref, []))}"
                            for st in brief.stories)
        _, user = self.rate_prompt.render(persona=self.p.persona, interests=render_interests(self.p, self.day),
                                          latent=render_latent(self.p), stories=stories)
        out, _ = await call_structured(
            self.client, role="sim.rate", system=self.rate_prompt.system, user=user, schema=SimRatings,
            trace=TraceCtx(run_id=self.run_id, role="sim.rate", step=f"{self.p.id}:d{self.day}"),
            cache_namespace=f"{self.cache_namespace}:{self.p.id}:{self.day}:rate",
        )
        refs = {s.story_ref for s in brief.stories}
        return {r.story_ref: r.rating for r in out.ratings if r.story_ref in refs}


def _group(sentences: list[BriefSentence]) -> list[tuple[str | None, list[BriefSentence]]]:
    """Consecutive runs of sentences by story_ref (None for intro/transition/outro)."""
    out: list[tuple[str | None, list[BriefSentence]]] = []
    for s in sentences:
        key = s.story_ref if s.kind == "story" else None
        if out and out[-1][0] == key:
            out[-1][1].append(s)
        else:
            out.append((key, [s]))
    return out
