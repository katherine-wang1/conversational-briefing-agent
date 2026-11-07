"""Live session WebSocket protocol (TDD §12.5). Also used, in text mode, by simulated users."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.contracts.common import Strict

SessionState = Literal[
    "READY", "PLAYING_BRIEF", "LISTENING", "THINKING", "SPEAKING_ANSWER", "OPEN_FLOOR", "ENDED"
]


# ---- client -> server ---------------------------------------------------------------------------


class ClientHello(Strict):
    type: Literal["hello"] = "hello"
    session_id: str
    client: dict = {}  # {"stt": "browser"|"server", "tts": "server"|"browser", "mode": "voice"|"text"}


class Playback(Strict):
    type: Literal["playback"] = "playback"
    segment_id: str
    offset_ms: int
    completed: bool = False  # additive (W-E): this segment finished playing; on the last one, the brief ended


class Interrupt(Strict):
    type: Literal["interrupt"] = "interrupt"
    segment_id: str | None = None  # None if nothing was playing
    offset_ms: int = 0


class Utterance(Strict):
    type: Literal["utterance"] = "utterance"
    text: str
    stt: Literal["browser", "server", "sim"] = "browser"
    end_of_speech_ts: float | None = None  # epoch seconds, for latency


class UtteranceAudio(Strict):
    type: Literal["utterance_audio"] = "utterance_audio"
    format: str  # e.g. "webm/opus"
    data_b64: str
    end_of_speech_ts: float | None = None


class Control(Strict):
    type: Literal["control"] = "control"
    action: Literal["skip", "next", "resume", "end", "play"]


class DebugSubscribe(Strict):
    """Additive: ask the server to send `debug` events for this (the caller's own) session."""

    type: Literal["debug_subscribe"] = "debug_subscribe"
    enabled: bool = True


ClientMessage = Annotated[
    ClientHello | Playback | Interrupt | Utterance | UtteranceAudio | Control | DebugSubscribe,
    Field(discriminator="type"),
]


# ---- server -> client ---------------------------------------------------------------------------


class StateMsg(Strict):
    type: Literal["state"] = "state"
    state: SessionState


class CitationSource(Strict):
    """A citation resolved for display: the source's name and a link to the article."""

    passage_id: str
    source: str | None = None  # e.g. "Reuters", or the site's domain
    url: str | None = None  # http(s) only
    title: str | None = None  # article title (or the story headline if unknown)


class TranscriptMsg(Strict):
    type: Literal["transcript"] = "transcript"
    role: Literal["user", "agent"]
    text: str
    turn_id: str
    citations: list[str] = []
    sources: list[CitationSource] = []  # additive: `citations` resolved (same order, deduped by url)


class TtsChunk(Strict):
    type: Literal["tts_chunk"] = "tts_chunk"
    seq: int
    format: str  # "mp3"
    data_b64: str
    sentence_id: str


class ResumeBrief(Strict):
    type: Literal["resume_brief"] = "resume_brief"
    segment_id: str


class BookmarkCreated(Strict):
    type: Literal["bookmark_created"] = "bookmark_created"
    question_id: str


class ErrorMsg(Strict):
    type: Literal["error"] = "error"
    code: str
    message: str


class DebugMsg(Strict):
    """Additive: a "what's happening" event for the live log, sent only after `debug_subscribe`.
    Kinds are state, heard, model, tool, speak, drop, memory, tts, error and info."""

    type: Literal["debug"] = "debug"
    kind: str
    text: str
    ts: float  # epoch seconds
    data: dict = {}


ServerMessage = Annotated[
    StateMsg | TranscriptMsg | TtsChunk | ResumeBrief | BookmarkCreated | ErrorMsg | DebugMsg,
    Field(discriminator="type"),
]
