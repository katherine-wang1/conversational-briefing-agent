"""Process settings from the environment (names match .env.example). Never logged."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    app_mode: Literal["live", "replay"] = "live"
    public_base_url: str = ""  # e.g. https://briefing.up.railway.app (for ws_url); derived if empty

    # database
    database_url: str = "postgresql://app_rw:app_rw@localhost:5432/briefing"
    database_admin_url: str = ""  # app_admin; admin routes are disabled if empty
    db_pool_min: int = 1
    db_pool_max: int = 10

    # auth
    supabase_url: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_secret: str = ""  # legacy HS256 projects only
    supabase_service_role_key: str = ""  # storage + auth-user deletion (server only)
    dev_auth_bypass: bool = False  # accepts "Bearer dev:<uuid>[:<email>]"; refused when app_env=prod

    # tools
    search_provider: Literal["tavily", "exa", "brave", ""] = ""
    search_api_key: str = ""
    archive_path: str = "eval/data/archive.sqlite"

    # speech
    tts_provider: Literal["openai", "fake", ""] = ""
    tts_api_key: str = ""
    tts_base_url: str = "https://api.openai.com/v1"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    stt_provider: Literal["groq", "openai", "fake", ""] = ""
    stt_api_key: str = ""
    stt_base_url: str = "https://api.groq.com/openai/v1"
    stt_model: str = "whisper-large-v3-turbo"

    # audio storage: "local" (dev; served under /media) or "supabase" (bucket brief-audio)
    audio_storage: Literal["local", "supabase"] = "local"
    audio_local_dir: str = "var/audio"
    audio_bucket: str = "brief-audio"

    # jobs
    worker_concurrency: int = 4
    worker_poll_s: float = 1.0
    agent_ports: Literal["auto", "real", "stub"] = "auto"
    voice_model: Literal["auto", "fake"] = "auto"  # fake = rule-based CorpusEchoModel (dev only)
    trace_dir: str = ""  # if set, agent run traces are written here as JSON (trace_uri on the row)
    credit_openrouter_usd: float = 200.0
    credit_tinker_usd: float = 100.0

    # api
    rate_limit_per_min: int = 60
    cors_origins: list[str] = Field(default_factory=list)

    @property
    def jwks_url(self) -> str:
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_url:
            return self.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
        return ""

    @property
    def dev_bypass_enabled(self) -> bool:
        return self.dev_auth_bypass and self.app_env != "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
