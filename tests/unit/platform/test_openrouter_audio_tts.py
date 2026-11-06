"""OpenRouterAudioTTS against a fake HTTP transport (no live calls)."""

from __future__ import annotations

import base64
import io
import json
import wave
from datetime import date
from uuid import uuid4

import httpx
import pytest

from app.config.settings import Settings
from app.contracts.brief import BriefScript, BriefSentence
from app.llm.ledger import Ledger, MemoryLedgerSink
from app.speech import OpenRouterAudioTTS, SpeechUnavailable, build_tts
from app.speech.openrouter_audio import SAMPLE_RATE, transcript_gap
from app.speech.render import render_brief_audio
from app.speech.storage import LocalDiskStorage

PCM = b"\x01\x00" * SAMPLE_RATE  # 1 s of near-silence


def sse(transcript: str, pcm: bytes = PCM, cost: float = 0.0004) -> bytes:
    half = len(pcm) // 2
    events = [
        {"choices": [{"delta": {"role": "assistant", "audio": {"transcript": transcript[:5]}}}]},
        {"choices": [{"delta": {"audio": {"data": base64.b64encode(pcm[:half]).decode()}}}]},
        {"choices": [{"delta": {"audio": {"transcript": transcript[5:],
                                          "data": base64.b64encode(pcm[half:]).decode()}}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 90, "completion_tokens": 120, "cost": cost,
                   "completion_tokens_details": {"audio_tokens": 100}}},
    ]
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode()


class FakeOpenRouter:
    def __init__(self, transcripts: list[str] | None = None, status: int = 200):
        self.transcripts = transcripts
        self.status = status
        self.bodies: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.bodies.append(body)
        assert req.headers["authorization"] == "Bearer test-key"
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        script = body["messages"][-1]["content"].removeprefix("<script>").removesuffix("</script>")
        t = self.transcripts.pop(0) if self.transcripts else script
        return httpx.Response(200, content=sse(t), headers={"content-type": "text/event-stream"})


def make(fake: FakeOpenRouter, **kw) -> tuple[OpenRouterAudioTTS, MemoryLedgerSink]:
    sink = MemoryLedgerSink()
    tts = OpenRouterAudioTTS(
        "test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(fake)), ledger=Ledger([sink]), **kw
    )
    return tts, sink


async def test_synthesize_returns_wav_and_records_spend():
    fake = FakeOpenRouter()
    tts, sink = make(fake, voice="nova")
    audio = await tts.synthesize("It has a 1-million-token context window.")
    assert audio.format == "wav"
    assert audio.duration_ms == 1000
    with wave.open(io.BytesIO(audio.data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SAMPLE_RATE)
        assert w.readframes(w.getnframes()) == PCM
    body = fake.bodies[0]
    assert body["model"] == "openai/gpt-audio-mini"
    assert body["modalities"] == ["text", "audio"]
    assert body["audio"] == {"voice": "nova", "format": "pcm16"}
    assert body["stream"] is True
    assert "temperature" not in body  # temperature 0 makes the audio run on (spike, 2026-11-06)
    assert body["max_tokens"] == 100 + 20 * 6
    assert body["messages"][-1]["content"] == "<script>It has a 1-million-token context window.</script>"
    [rec] = sink.records
    assert (rec.role, rec.provider, rec.tokens_in, rec.tokens_out) == ("tts", "openrouter", 90, 120)
    assert rec.ok
    assert rec.cost_usd == pytest.approx(0.0004) and rec.stream and rec.ttft_ms is not None


async def test_voice_override_per_call():
    fake = FakeOpenRouter()
    tts, _ = make(fake)
    await tts.synthesize("Hello there.", voice="shimmer")
    assert fake.bodies[0]["audio"]["voice"] == "shimmer"


async def test_dropped_words_trigger_one_retry_then_pass():
    text = "Here is the short answer: it has a 1-million-token context window."
    fake = FakeOpenRouter(["It has a 1-million-token context window.", text])
    tts, sink = make(fake)
    audio = await tts.synthesize(text)
    assert audio.format == "wav" and len(fake.bodies) == 2
    assert [r.kind for r in sink.records] == ["call", "repair"]


async def test_persistent_mismatch_raises():
    text = "Here is the short answer: it has a 1-million-token context window."
    fake = FakeOpenRouter(["It has a 1-million-token context window."] * 2)
    tts, _ = make(fake)
    with pytest.raises(SpeechUnavailable, match="did not match"):
        await tts.synthesize(text)
    assert len(fake.bodies) == 2


async def test_runaway_audio_is_rejected():
    class Runaway(FakeOpenRouter):
        def __call__(self, req: httpx.Request) -> httpx.Response:
            self.bodies.append(json.loads(req.content))
            return httpx.Response(200, content=sse("Hello.", pcm=PCM * 30))  # 30 s for one word

    fake = Runaway()
    tts, sink = make(fake)
    with pytest.raises(SpeechUnavailable, match="audio 30000 ms"):
        await tts.synthesize("Hello.")
    assert len(fake.bodies) == 2 and len(sink.records) == 2


async def test_http_error_raises_and_is_recorded():
    tts, sink = make(FakeOpenRouter(status=400))
    with pytest.raises(SpeechUnavailable, match="HTTP 400"):
        await tts.synthesize("Hello.")
    assert sink.records[0].ok is False


async def test_spend_cap_blocks_call():
    fake = FakeOpenRouter()
    sink = MemoryLedgerSink()
    ledger = Ledger([sink], spend_cap_usd=0.0)
    tts = OpenRouterAudioTTS("test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
                             ledger=ledger)
    with pytest.raises(SpeechUnavailable, match="spend cap"):
        await tts.synthesize("Hello.")
    assert fake.bodies == []


def test_transcript_gap_ignores_punctuation_and_homophone_spelling():
    exp = "Ricursive estimates development cost was 32 million dollars, about one-tenth of the leader."
    assert transcript_gap(exp, exp.replace("Ricursive", "Recursive").replace(",", "")) == 0
    assert transcript_gap("You can adjust a GIF's speed.", "You can adjust a GIF’s speed") == 0
    assert transcript_gap("Here is the short answer: it is big.", "It is big.") == 5


def test_build_tts_selects_openrouter_audio(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    ledger = Ledger([MemoryLedgerSink()])
    s = Settings(_env_file=None, tts_provider="openrouter_audio", tts_voice="echo")
    tts = build_tts(s, ledger=ledger)
    assert isinstance(tts, OpenRouterAudioTTS)
    assert (tts.model, tts.voice, tts.ledger) == ("openai/gpt-audio-mini", "echo", ledger)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert build_tts(Settings(_env_file=None, tts_provider="openrouter_audio")) is None


async def test_render_brief_audio_writes_wav_segments(tmp_path):
    script = BriefScript(
        brief_id="br_test", user_id=str(uuid4()), local_date=date(2026, 11, 6), stories=[],
        est_duration_s=10,
        sentences=[
            BriefSentence(sid="s000", text="Good morning.", kind="intro"),
            BriefSentence(sid="s001", text="It has a 1-million-token context window. [p:abc]", kind="story",
                          story_ref="st1"),
        ],
    )
    fake = FakeOpenRouter()
    tts, _ = make(fake)
    manifest = await render_brief_audio(script, tts, LocalDiskStorage(str(tmp_path)))
    assert [s.url.rsplit("/", 1)[-1] for s in manifest.segments] == ["s000.wav", "s001.wav"]
    assert all(s.duration_ms == 1000 for s in manifest.segments)
    assert (tmp_path / manifest.segments[1].url).read_bytes()[:4] == b"RIFF"
    sent = sorted(b["messages"][-1]["content"] for b in fake.bodies)
    assert "<script>It has a 1-million-token context window.</script>" in sent  # citations stripped
