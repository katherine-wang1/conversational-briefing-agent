"""Live session state machine (TDD §12.1) over the protocol in app/contracts/session.py.

    READY ──play──► PLAYING_BRIEF ──interrupt──► LISTENING ──utterance──► THINKING ──► SPEAKING_ANSWER
                         │ end of brief                                                    │
                         ▼                                                                 ▼
                    OPEN_FLOOR ◄──────────────── (no brief to resume) ◄──── answer done ───┤
                                                  PLAYING_BRIEF ◄── auto-resume ───────────┘
    any ──"end" / idle / disconnect──► ENDED → enqueue memory_update

Transport-agnostic: `handle(msg)` takes a ClientMessage, `send` delivers ServerMessages.

Protocol notes for clients (W-F web app, W-G simulator):
- Voice mode: the client plays the brief from the AudioManifest and reports `playback`
  (segment_id, offset_ms) every ~2 s; `playback.completed=true` on the last segment ends the brief.
- Text mode (`hello.client.mode == "text"`, used by simulated users): on `play` / resume the server
  sends the brief sentences from the current segment as `transcript` messages (role agent,
  turn_id = segment id). The client "reads" them and may `interrupt` at any segment, or send
  `playback{segment_id: <last>, completed: true}` when done. No `tts_chunk`s in text mode.
- Answers stream as one `transcript` message per sentence (same turn_id), each followed, in voice
  mode with server TTS, by a `tts_chunk` (sentence_id = "<turn_id>-<n>").
- After an answer, if the brief was interrupted, the server auto-resumes after a short pause:
  `resume_brief{segment_id}` = restart that segment from 0 (the start of the interrupted sentence).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.agents.voice.context import position_line, prefix_messages, turn_messages, voice_prompt
from app.agents.voice.store import SessionStore
from app.agents.voice.tools import NAV_ACTIONS, PREFERENCE_KINDS, VOICE_TOOLS
from app.contracts.brief import BriefScript, SourcePack
from app.contracts.common import BudgetExceeded, TokenBudget, TraceCtx
from app.contracts.llm import ChatRequest, Message, ModelClient, ToolCall
from app.contracts.session import (
    BookmarkCreated,
    ClientHello,
    Control,
    ErrorMsg,
    Interrupt,
    Playback,
    ResumeBrief,
    SessionState,
    StateMsg,
    TranscriptMsg,
    TtsChunk,
    Utterance,
    UtteranceAudio,
)
from app.contracts.tools import Tool, ToolContext
from app.db.ids import new_id
from app.speech.base import SpeechUnavailable, STTProvider, TTSProvider
from app.speech.sentence_splitter import SentenceSplitter, strip_citations

log = logging.getLogger(__name__)

Send = Callable[[Any], Awaitable[None]]


@dataclass
class VoiceConfig:
    auto_resume: bool = True
    auto_resume_delay_s: float = 1.5
    idle_end_s: float = 90.0
    quick_search_timeout_s: float = 3.0
    max_quick_searches: int = 2
    max_tool_rounds: int = 3
    turn_input_tokens: int = 30_000
    filler: str = "Let me check."
    resume_prompt: str = "Back to the brief?"
    voice: str | None = None

    @classmethod
    def from_agent_config(cls, agent_cfg: dict[str, Any], **overrides: Any) -> VoiceConfig:
        v = agent_cfg.get("voice", {})
        b = (agent_cfg.get("budgets") or {}).get("voice_turn", {})
        kw: dict[str, Any] = {
            "auto_resume_delay_s": v.get("auto_resume_delay_s", 1.5),
            "idle_end_s": v.get("idle_end_s", 90),
            "quick_search_timeout_s": b.get("quick_search_timeout_s", 3),
            "max_quick_searches": b.get("quick_searches", 2),
            "turn_input_tokens": b.get("input_tokens", 30_000),
        }
        kw.update(overrides)
        return cls(**kw)


class _Ended(Exception):
    pass


class VoiceSession:
    def __init__(
        self,
        *,
        session_id: str,
        user_id: Any,
        brief: BriefScript | None,
        packs: list[SourcePack],
        model: ModelClient,
        tools: dict[str, Tool],
        store: SessionStore,
        send: Send,
        tool_ctx: Callable[[TokenBudget], ToolContext],
        tts: TTSProvider | None = None,
        stt: STTProvider | None = None,
        config: VoiceConfig | None = None,
        run_id: str = "live",
        open_questions: str = "",
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.brief = brief
        self.packs = packs
        self.model = model
        self.tools = tools
        self.store = store
        self._send = send
        self.tool_ctx = tool_ctx
        self.tts = tts
        self.stt = stt
        self.cfg = config or VoiceConfig()
        self.run_id = run_id
        self.open_questions = open_questions

        self.state: SessionState = "READY"
        self.text_mode = False
        self.position: dict[str, Any] | None = None  # last playback {segment_id, offset_ms}
        self.interrupted_at: dict[str, Any] | None = None
        self.brief_completed = False
        self.furthest_idx = -1
        self.history: list[tuple[str, str]] = []
        self.turn_seq = 0
        self.tts_seq = 0
        self.asked_about: dict[str, int] = {}
        self.ended = False
        self._prefix = prefix_messages(brief, packs)
        self._turn_task: asyncio.Task | None = None
        self._resume_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._sids = [s.sid for s in brief.sentences] if brief else []

    # ---- plumbing -----------------------------------------------------------------------------------

    async def send(self, msg: Any) -> None:
        async with self._send_lock:
            await self._send(msg)

    async def set_state(self, state: SessionState) -> None:
        if state == self.state and state != "READY":
            return
        self.state = state
        await self.send(StateMsg(state=state))
        if state in ("OPEN_FLOOR", "LISTENING"):
            self._arm_idle()
        elif self._idle_task:
            self._idle_task.cancel()

    def _arm_idle(self) -> None:
        if self._idle_task:
            self._idle_task.cancel()
        if self.cfg.idle_end_s > 0:
            self._idle_task = asyncio.create_task(self._idle_timer())

    async def _idle_timer(self) -> None:
        await asyncio.sleep(self.cfg.idle_end_s)
        await self.end("idle")

    def _story_of(self, sid: str | None) -> str | None:
        if not self.brief or sid not in self._sids:
            return None
        return self.brief.sentences[self._sids.index(sid)].story_ref

    def _current_sid(self) -> str | None:
        pos = self.interrupted_at or self.position
        return pos["segment_id"] if pos else None

    def _valid_story(self, story_id: str | None) -> str | None:
        """Voice tools may only reference stories in this user's brief (never arbitrary ids)."""
        if not story_id or not self.brief:
            return None
        return story_id if story_id in {s.story_ref for s in self.brief.stories} else None

    def _note_position(self, sid: str | None, offset_ms: int = 0) -> None:
        if sid is None:
            return
        self.position = {"segment_id": sid, "offset_ms": offset_ms}
        if sid in self._sids:
            self.furthest_idx = max(self.furthest_idx, self._sids.index(sid))

    async def _signal(
        self,
        type_: str,
        *,
        story_ref: str | None = None,
        explicit: bool = False,
        strength: float = 1.0,
        turn_id: str | None = None,
        **payload: Any,
    ) -> None:
        await self.store.add_signal(
            {
                "type": type_,
                "story_ref": story_ref,
                "explicit": explicit,
                "strength": strength,
                "turn_id": turn_id,
                "payload": payload,
            }
        )

    async def _error(self, code: str, message: str) -> None:
        await self.send(ErrorMsg(code=code, message=message))

    # ---- inbound --------------------------------------------------------------------------------------

    async def handle(self, msg: Any) -> None:
        if self.ended:
            await self._error("session_ended", "this session has ended")
            return
        if self.state in ("OPEN_FLOOR", "LISTENING"):
            self._arm_idle()
        if isinstance(msg, ClientHello):
            await self._on_hello(msg)
        elif isinstance(msg, Playback):
            await self._on_playback(msg)
        elif isinstance(msg, Interrupt):
            await self._on_interrupt(msg)
        elif isinstance(msg, Utterance):
            await self._start_turn(msg.text, msg.end_of_speech_ts)
        elif isinstance(msg, UtteranceAudio):
            await self._on_audio(msg)
        elif isinstance(msg, Control):
            await self._on_control(msg)

    async def _on_hello(self, msg: ClientHello) -> None:
        if msg.session_id != self.session_id:
            await self._error("bad_session", "session_id does not match this connection")
            return
        self.text_mode = msg.client.get("mode") == "text" or msg.client.get("tts") == "browser"
        await self.store.update_session(client=msg.client)
        self.state = "READY"
        await self.send(StateMsg(state="READY"))

    async def _on_playback(self, msg: Playback) -> None:
        self._note_position(msg.segment_id, msg.offset_ms)
        if (
            msg.completed
            and self._sids
            and msg.segment_id == self._sids[-1]
            and self.state == "PLAYING_BRIEF"
        ):
            self.brief_completed = True
            self.interrupted_at = None
            await self.store.update_session(position=self.position)
            await self.set_state("OPEN_FLOOR")

    async def _on_interrupt(self, msg: Interrupt) -> None:
        was = self.state
        await self._cancel_tasks()
        if was == "PLAYING_BRIEF":
            sid = msg.segment_id or (self.position or {}).get("segment_id")
            if sid:
                self._note_position(sid, msg.offset_ms)
                self.interrupted_at = {"segment_id": sid, "offset_ms": msg.offset_ms}
                await self.store.update_session(position=self.interrupted_at)
        await self.store.add_turn(
            {
                "id": new_id("t"),
                "seq": self._next_seq(),
                "role": "system",
                "action": "interrupt",
                "story_ref": self._story_of(msg.segment_id),
                "playback_pos": {"segment_id": msg.segment_id, "offset_ms": msg.offset_ms, "state": was},
            }
        )
        await self.set_state("LISTENING")

    async def _on_audio(self, msg: UtteranceAudio) -> None:
        if self.stt is None:
            await self._error("stt_unavailable", "server speech-to-text is not configured; use browser STT")
            return
        try:
            text = await self.stt.transcribe(base64.b64decode(msg.data_b64), format=msg.format)
        except SpeechUnavailable as e:
            await self._error("stt_failed", str(e))
            return
        if not text.strip():
            await self._error("stt_empty", "didn't catch that")
            return
        await self._start_turn(text, msg.end_of_speech_ts, stt="server")

    async def _on_control(self, msg: Control) -> None:
        if msg.action == "end":
            await self.end("natural" if self.brief_completed else "cut_short")
        elif msg.action == "play":
            await self._cancel_tasks()
            start = self._current_sid() if not self.brief_completed else None
            await self._play_from(start or (self._sids[0] if self._sids else None))
        elif msg.action == "resume":
            await self._cancel_tasks()
            await self._resume()
        elif msg.action in ("skip", "next"):
            await self._cancel_tasks()
            await self._skip_story()

    # ---- brief playback -------------------------------------------------------------------------------

    async def _play_from(self, sid: str | None, *, announce: bool = False) -> None:
        if sid is None or sid not in self._sids:
            self.brief_completed = True
            await self.set_state("OPEN_FLOOR")
            return
        self.interrupted_at = None
        self._note_position(sid, 0)
        if announce:
            await self.send(ResumeBrief(segment_id=sid))
        await self.set_state("PLAYING_BRIEF")
        if self.text_mode and self.brief:
            for s in self.brief.sentences[self._sids.index(sid) :]:
                text, cites = strip_citations(s.text)
                await self.send(
                    TranscriptMsg(role="agent", text=text, turn_id=s.sid, citations=s.cites or cites)
                )

    async def _resume(self) -> None:
        sid = (self.interrupted_at or self.position or {}).get("segment_id")
        if sid is None or self.brief_completed:
            await self.set_state("OPEN_FLOOR")
            return
        await self._play_from(sid, announce=True)

    async def _skip_story(self) -> None:
        sid = self._current_sid()
        story = self._story_of(sid)
        if story:
            await self._signal("skip", story_ref=story)
        idx = self._sids.index(sid) if sid in self._sids else -1
        nxt = None
        if self.brief:
            for s in self.brief.sentences[idx + 1 :]:
                if s.story_ref and s.story_ref != story:
                    nxt = s.sid
                    break
        await self._play_from(nxt, announce=True)

    # ---- turns ------------------------------------------------------------------------------------------

    def _next_seq(self) -> int:
        self.turn_seq += 1
        return self.turn_seq

    async def _cancel_tasks(self) -> None:
        for t in (self._turn_task, self._resume_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._turn_task = self._resume_task = None

    async def _start_turn(self, text: str, end_of_speech_ts: float | None, stt: str = "client") -> None:
        if self.state == "PLAYING_BRIEF" and self.interrupted_at is None and self.position:
            self.interrupted_at = dict(self.position)  # implicit interrupt (barge-in)
        await self._cancel_tasks()
        self._turn_task = asyncio.create_task(self._run_turn(text, end_of_speech_ts))

    async def _run_turn(self, text: str, end_of_speech_ts: float | None) -> None:
        t0 = time.time()
        turn_id = new_id("t")
        await self.set_state("THINKING")
        target = self._story_of(self._current_sid()) if not self.brief_completed else None
        await self.store.add_turn(
            {
                "id": turn_id + "u",
                "seq": self._next_seq(),
                "role": "user",
                "text": text,
                "story_ref": target,
                "playback_pos": self.interrupted_at,
                "timings": {"end_of_speech_ts": end_of_speech_ts},
            }
        )
        await self.send(TranscriptMsg(role="user", text=text, turn_id=turn_id + "u"))
        self.history.append(("user", text))

        spoken: list[str] = []
        citations: list[str] = []
        nav: list[tuple[str, str | None]] = []
        timings: dict[str, Any] = {}
        n_sent = 0
        story_hits: set[str] = set()
        aborted = False

        async def speak(sentence: str) -> None:
            nonlocal n_sent
            clean, cites = strip_citations(sentence)
            if not clean:
                return
            if "first_sentence_ms" not in timings:
                timings["first_sentence_ms"] = int((time.time() - t0) * 1000)
                if end_of_speech_ts:
                    timings["eos_to_first_sentence_ms"] = int((time.time() - end_of_speech_ts) * 1000)
                await self.set_state("SPEAKING_ANSWER")
            n_sent += 1
            spoken.append(clean)
            citations.extend(c for c in cites if c not in citations)
            await self.send(TranscriptMsg(role="agent", text=clean, turn_id=turn_id, citations=cites))
            if self.tts is not None and not self.text_mode:
                try:
                    audio = await self.tts.synthesize(clean, voice=self.cfg.voice)
                except SpeechUnavailable as e:
                    await self._error("tts_failed", str(e))
                    return
                self.tts_seq += 1
                await self.send(
                    TtsChunk(
                        seq=self.tts_seq,
                        format=audio.format,
                        sentence_id=f"{turn_id}-{n_sent}",
                        data_b64=base64.b64encode(audio.data).decode(),
                    )
                )

        messages = turn_messages(
            self._prefix,
            self.history[:-1],
            position_line(self.brief, self._current_sid(), self.brief_completed),
            text,
            self.open_questions,
        )
        budget = TokenBudget(input_tokens=self.cfg.turn_input_tokens, searches=self.cfg.max_quick_searches)
        searches = 0
        filler_said = False
        try:
            for _round in range(self.cfg.max_tool_rounds):
                req = ChatRequest(
                    role="voice.turn",
                    messages=messages,
                    tools=VOICE_TOOLS,
                    max_output_tokens=400,
                    budget=budget,
                    trace=TraceCtx(run_id=self.run_id, user_id=self.user_id, role="voice.turn", step=turn_id),
                    cache_namespace=self.run_id,
                )
                splitter = SentenceSplitter()
                round_text: list[str] = []
                calls: list[ToolCall] = []
                async for ev in self.model.stream(req):
                    if ev.type == "text_delta" and ev.text:
                        round_text.append(ev.text)
                        for s in splitter.feed(ev.text):
                            await speak(s)
                    elif ev.type == "tool_call" and ev.tool_call:
                        calls.append(ev.tool_call)
                    elif ev.type == "done" and ev.response:
                        if "ttft_ms" not in timings:
                            timings["model_latency_ms"] = ev.response.latency_ms
                        for c in ev.response.tool_calls:
                            if all(c.id != x.id for x in calls):
                                calls.append(c)
                    elif ev.type == "error":
                        raise RuntimeError(ev.error or "model error")
                for s in splitter.flush():
                    await speak(s)
                if not calls:
                    break
                messages.append(Message(role="assistant", content="".join(round_text), tool_calls=calls))
                for call in calls:
                    sid_arg = self._valid_story(call.arguments.get("story_id"))
                    if sid_arg:
                        story_hits.add(sid_arg)
                    if call.name == "quick_search":
                        if searches >= self.cfg.max_quick_searches:
                            result = "<tool_error>quick_search limit reached for this turn</tool_error>"
                        else:
                            searches += 1
                            if not filler_said:
                                filler_said = True
                                await speak(self.cfg.filler)
                            result = await self._quick_search(str(call.arguments.get("query", "")), budget)
                    else:
                        result = await self._run_voice_tool(call, turn_id, nav)
                    messages.append(Message(role="tool", tool_call_id=call.id, content=result))
                if nav:
                    break
        except asyncio.CancelledError:
            aborted = True
            raise
        except BudgetExceeded:
            await speak("Sorry, I can't look into that right now.")
        except Exception as e:  # noqa: BLE001
            log.exception("voice turn failed")
            await self._error("turn_failed", type(e).__name__)
            if not spoken:
                await speak("Sorry, something went wrong there.")
        finally:
            timings["total_ms"] = int((time.time() - t0) * 1000)
            answer = " ".join(spoken)
            if answer:
                self.history.append(("agent", answer))
            await asyncio.shield(
                self._finish_turn(turn_id, answer, citations, timings, aborted, target, story_hits)
            )
        await self._after_turn(nav)

    async def _finish_turn(
        self,
        turn_id: str,
        answer: str,
        citations: list[str],
        timings: dict[str, Any],
        aborted: bool,
        target: str | None,
        story_hits: set[str],
    ) -> None:
        await self.store.add_turn(
            {
                "id": turn_id,
                "seq": self._next_seq(),
                "role": "agent",
                "text": answer,
                "citations": citations,
                "action": "interrupted" if aborted else "answer",
                "story_ref": target,
                "timings": timings,
            }
        )
        for story in story_hits or ({target} if target else set()):
            n = self.asked_about.get(story, 0)
            self.asked_about[story] = n + 1
            await self._signal("dive" if n == 0 else "followup", story_ref=story, turn_id=turn_id)

    async def _after_turn(self, nav: list[tuple[str, str | None]]) -> None:
        for action, story in nav:
            if action == "end_session":
                await self.end("natural" if self.brief_completed else "cut_short")
                return
            if action == "resume_brief":
                await self._resume()
                return
            if action in ("skip_story", "next_story"):
                if story and self.brief:
                    first = next((s.sid for s in self.brief.sentences if s.story_ref == story), None)
                    self.interrupted_at = (
                        {"segment_id": first, "offset_ms": 0} if first else self.interrupted_at
                    )
                await self._skip_story() if action == "skip_story" else await self._next_story(story)
                return
            if action == "repeat":
                sid = self._current_sid()
                story_ref = story or self._story_of(sid)
                first = (
                    next((s.sid for s in self.brief.sentences if s.story_ref == story_ref), sid)
                    if self.brief
                    else sid
                )
                await self._play_from(first, announce=True)
                return
        if self.interrupted_at and not self.brief_completed:
            if self.cfg.auto_resume:
                self._resume_task = asyncio.create_task(self._auto_resume())
            else:
                await self.set_state("OPEN_FLOOR")
        else:
            await self.set_state("OPEN_FLOOR")

    async def _next_story(self, story: str | None) -> None:
        if story and self.brief:
            first = next((s.sid for s in self.brief.sentences if s.story_ref == story), None)
            if first:
                await self._play_from(first, announce=True)
                return
        await self._skip_story()

    async def _auto_resume(self) -> None:
        await asyncio.sleep(self.cfg.auto_resume_delay_s)
        await self._resume()

    # ---- tools ------------------------------------------------------------------------------------------

    async def _quick_search(self, query: str, budget: TokenBudget) -> str:
        tool = self.tools.get("search")
        if tool is None or not query:
            return "<tool_error>search unavailable</tool_error>"
        ctx = self.tool_ctx(budget)
        try:
            r = await asyncio.wait_for(tool(ctx, query=query, max_results=5), self.cfg.quick_search_timeout_s)
        except TimeoutError:
            return (
                "<tool_error>search timed out; answer without it or offer to bookmark the question"
                "</tool_error>"
            )
        return r.as_untrusted_text()

    async def _run_voice_tool(self, call: ToolCall, turn_id: str, nav: list[tuple[str, str | None]]) -> str:
        a = call.arguments
        story = self._valid_story(a.get("story_id"))
        if a.get("story_id") and story is None:
            bad = str(a.get("story_id"))[:40]
            return f"<tool_error>unknown story_id {bad!r}: use one from the brief</tool_error>"
        if call.name == "bookmark_question":
            q = str(a.get("question", "")).strip()
            if not q:
                return "<tool_error>question is required</tool_error>"
            story = story or self._story_of(self._current_sid())
            qid = await self.store.create_question(
                q[:500], story, {"session_id": self.session_id, "turn_id": turn_id}
            )
            await self._signal("bookmark", story_ref=story, explicit=True, turn_id=turn_id, question_id=qid)
            await self.send(BookmarkCreated(question_id=qid))
            return "bookmarked; tell the listener you'll follow up in the next brief"
        if call.name == "save_for_later":
            if story is None:
                return "<tool_error>story_id is required</tool_error>"
            await self.store.save_item(story)
            await self._signal("save_for_later", story_ref=story, explicit=True, turn_id=turn_id)
            return "saved"
        if call.name == "note_preference":
            kind = a.get("kind")
            if kind not in PREFERENCE_KINDS:
                return f"<tool_error>kind must be one of {PREFERENCE_KINDS}</tool_error>"
            value = str(a.get("value", ""))[:200]
            if kind == "time_available":
                secs = _seconds(value)
                if secs:
                    await self.store.update_session(time_available_s=secs)
            await self._signal(kind, story_ref=story, explicit=True, turn_id=turn_id, value=value)
            return "noted"
        if call.name == "navigate":
            action = a.get("action")
            if action not in NAV_ACTIONS:
                return f"<tool_error>action must be one of {NAV_ACTIONS}</tool_error>"
            nav.append((action, story))
            return "ok"
        return f"<tool_error>unknown tool {call.name}</tool_error>"

    # ---- end ----------------------------------------------------------------------------------------------

    async def end(self, reason: str) -> None:
        if self.ended:
            return
        self.ended = True
        current = asyncio.current_task()
        for t in (self._turn_task, self._resume_task, self._idle_task):
            if t and not t.done() and t is not current:
                t.cancel()
        if self.brief and not self.brief_completed:
            reached = {s.story_ref for s in self.brief.sentences[: self.furthest_idx + 1] if s.story_ref}
            for st in self.brief.stories:
                if st.story_ref not in reached:
                    await self._signal("not_reached", story_ref=st.story_ref, strength=0.0)
        await self.store.update_session(
            ended_at=datetime.now(UTC), end_reason=reason, state="ENDED", position=self.position
        )
        await self.store.on_end()  # persist everything before telling the client (it may hang up at once)
        self.state = "ENDED"
        try:
            await self.send(StateMsg(state="ENDED"))
        except Exception:  # noqa: BLE001 - the socket may already be gone
            pass


def _seconds(value: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(h|hour|m|min|minute|s|sec|second)?", value.lower())
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "s")[0]
    return int(n * {"h": 3600, "m": 60, "s": 1}[unit])


__all__ = ["VoiceConfig", "VoiceSession", "voice_prompt"]
