"""HTTP speech providers. Keys come from env (TTS_API_KEY, STT_API_KEY); see app/config/settings.py.

- `OpenAICompatibleTTS`: POST {base}/audio/speech (OpenAI's API shape; other vendors expose the same).
- `WhisperSTT`: POST {base}/audio/transcriptions, multipart (Groq Whisper by default, or OpenAI).
"""

from __future__ import annotations

import httpx

from app.speech.base import SpeechUnavailable, TtsAudio, estimate_duration_ms


class OpenAICompatibleTTS:
    name = "openai_tts"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini-tts",
        voice: str = "alloy",
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 20.0,
    ):
        if not api_key:
            raise SpeechUnavailable("TTS_API_KEY is not set")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.client = client or httpx.AsyncClient(timeout=timeout_s)

    async def synthesize(self, text: str, *, voice: str | None = None) -> TtsAudio:
        try:
            r = await self.client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "voice": voice or self.voice,
                    "input": text,
                    "response_format": "mp3",
                },
            )
        except httpx.HTTPError as e:
            raise SpeechUnavailable(f"TTS request failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise SpeechUnavailable(f"TTS provider returned HTTP {r.status_code}")
        return TtsAudio(data=r.content, format="mp3", duration_ms=estimate_duration_ms(text))


class WhisperSTT:
    name = "whisper_stt"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.groq.com/openai/v1",
        model: str = "whisper-large-v3-turbo",
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 20.0,
    ):
        if not api_key:
            raise SpeechUnavailable("STT_API_KEY is not set")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=timeout_s)

    async def transcribe(self, audio: bytes, *, format: str) -> str:
        ext = (format.split("/")[0] or "webm").split(";")[0]
        try:
            r = await self.client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={"model": self.model, "response_format": "json"},
                files={"file": (f"utterance.{ext}", audio, f"audio/{ext}")},
            )
        except httpx.HTTPError as e:
            raise SpeechUnavailable(f"STT request failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise SpeechUnavailable(f"STT provider returned HTTP {r.status_code}")
        return str(r.json().get("text", "")).strip()
