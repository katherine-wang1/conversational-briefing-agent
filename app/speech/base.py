"""Speech provider interfaces (TDD §13.3). Swap providers without touching callers."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class SpeechUnavailable(RuntimeError):
    """No provider configured (missing key) or the provider failed."""


class TtsAudio(BaseModel):
    data: bytes
    format: str = "mp3"
    duration_ms: int


class TTSProvider(Protocol):
    name: str

    async def synthesize(self, text: str, *, voice: str | None = None) -> TtsAudio: ...


class STTProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, *, format: str) -> str: ...


def estimate_duration_ms(text: str) -> int:
    """Spoken-length estimate (~2.7 words/s) when the provider doesn't report one."""
    words = max(1, len(text.split()))
    return int(words / 2.7 * 1000) + 250
