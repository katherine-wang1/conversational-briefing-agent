"""A key that is only in `.env` must reach the model layer (which reads os.environ directly)."""

from __future__ import annotations

import os

from app.config.env_file import load_env_file
from app.llm.factory import get_client


def test_key_only_in_env_file_gives_a_real_model_client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_SPEND_CAP_USD", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-test-only-in-dotenv\nLLM_SPEND_CAP_USD=2\n")
    assert get_client(ledger_path=str(tmp_path / "l.jsonl")) is None  # before: .env invisible here
    try:
        assert load_env_file() is not None
        assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test-only-in-dotenv"
        assert get_client(ledger_path=str(tmp_path / "l.jsonl")) is not None
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("LLM_SPEND_CAP_USD", None)


def test_real_environment_wins_and_missing_file_is_fine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_env_file() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=from-file\n")
    load_env_file()
    assert os.environ["OPENROUTER_API_KEY"] == "from-env"
