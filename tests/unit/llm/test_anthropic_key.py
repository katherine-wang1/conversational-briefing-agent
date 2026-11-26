import pytest

from app.llm.anthropic_api import AnthropicUnavailable, api_key_from_env, make_async_client


def test_key_prefers_anthropic_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("CLAUDE_API_KEY", "b")
    assert api_key_from_env() == "a"


def test_key_falls_back_to_claude_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "b")
    assert api_key_from_env() == "b"


def test_no_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    with pytest.raises(AnthropicUnavailable):
        make_async_client()


def test_settings_read_claude_api_key(monkeypatch):
    from app.config.settings import Settings

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "b")
    assert Settings(_env_file=None).anthropic_api_key == "b"
