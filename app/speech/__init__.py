"""Speech adapters (TDD §13.3): TTS / STT provider interfaces, HTTP + fake implementations,
sentence splitting, audio storage and the brief pre-render."""

from __future__ import annotations

from app.config.settings import Settings
from app.speech.base import SpeechUnavailable, STTProvider, TtsAudio, TTSProvider
from app.speech.fake import FakeSTT, FakeTTS
from app.speech.http import OpenAICompatibleTTS, WhisperSTT
from app.speech.storage import AudioStorage, LocalDiskStorage, SupabaseStorage

__all__ = [
    "AudioStorage",
    "FakeSTT",
    "FakeTTS",
    "LocalDiskStorage",
    "OpenAICompatibleTTS",
    "STTProvider",
    "SpeechUnavailable",
    "SupabaseStorage",
    "TTSProvider",
    "TtsAudio",
    "WhisperSTT",
    "build_storage",
    "build_stt",
    "build_tts",
]


def build_tts(s: Settings) -> TTSProvider | None:
    """None when no provider is configured: briefs are then served without audio and the client
    falls back to browser speechSynthesis (dev only)."""
    if s.tts_provider == "fake":
        return FakeTTS()
    if s.tts_api_key and s.tts_provider in ("openai", ""):
        return OpenAICompatibleTTS(
            s.tts_api_key, base_url=s.tts_base_url, model=s.tts_model, voice=s.tts_voice
        )
    return None


def build_stt(s: Settings) -> STTProvider | None:
    if s.stt_provider == "fake":
        return FakeSTT()
    if s.stt_api_key:
        return WhisperSTT(s.stt_api_key, base_url=s.stt_base_url, model=s.stt_model)
    return None


def build_storage(s: Settings) -> AudioStorage:
    if s.audio_storage == "supabase":
        return SupabaseStorage(s.supabase_url, s.supabase_service_role_key, s.audio_bucket)
    return LocalDiskStorage(s.audio_local_dir)
