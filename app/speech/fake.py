"""Fake speech providers for tests and local dev without keys. Output is obviously not audio."""

from __future__ import annotations

import base64

from app.speech.base import TtsAudio, estimate_duration_ms


class FakeTTS:
    name = "fake_tts"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str, *, voice: str | None = None) -> TtsAudio:
        self.calls.append(text)
        return TtsAudio(
            data=b"FAKE-MP3:" + text.encode(), format="mp3", duration_ms=estimate_duration_ms(text)
        )


class FakeSTT:
    """'Transcribes' audio that is just base64-free UTF-8 text (tests send text bytes as audio)."""

    name = "fake_stt"

    async def transcribe(self, audio: bytes, *, format: str) -> str:
        try:
            return audio.decode()
        except UnicodeDecodeError:
            return base64.b64encode(audio[:12]).decode()
