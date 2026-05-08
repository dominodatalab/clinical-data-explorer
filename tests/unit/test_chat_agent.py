"""Unit tests for chat agent helpers."""

import asyncio

import pytest

import chat_agent


@pytest.fixture(autouse=True)
def reset_chat_agent_state(monkeypatch):
    for env_var in ("LLM_BASE_URL", "LLM_API_KEY", "OPENAI_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(env_var, raising=False)

    chat_agent.get_message_histories.cache_clear()
    chat_agent._llm_model = None
    yield
    chat_agent.get_message_histories.cache_clear()
    chat_agent._llm_model = None


class FakeResult:
    def __init__(self, output, new_messages):
        self.output = output
        self._new_messages = new_messages

    def new_messages(self):
        return self._new_messages


class FakeAgent:
    def __init__(self, result, enter_error=None):
        self.result = result
        self.enter_error = enter_error
        self.run_calls = []

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def run(self, message, message_history=None):
        self.run_calls.append(
            {
                "message": message,
                "message_history": message_history,
                "message_history_snapshot": (
                    list(message_history) if message_history is not None else None
                ),
            }
        )
        return self.result


def test_get_llm_config_uses_defaults_and_openai_key_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    assert chat_agent.get_llm_config() == (
        "https://api.openai.com/v1",
        "openai-key",
        "gpt-4o-mini",
    )


def test_get_llm_config_prefers_llm_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    assert chat_agent.get_llm_config() == (
        "https://llm.example/v1",
        "llm-key",
        "test-model",
    )


def test_is_chat_configured_requires_api_key_for_remote_provider(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    assert chat_agent.is_chat_configured() is False

    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    assert chat_agent.is_chat_configured() is True


def test_is_chat_configured_allows_local_provider_without_api_key(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3")

    assert chat_agent.is_chat_configured() is True


def test_get_chat_status_hides_remote_config_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    assert chat_agent.get_chat_status() == {
        "configured": False,
        "base_url": None,
        "model": None,
        "is_local": False,
        "missing": ["LLM_API_KEY"],
    }


def test_get_message_histories_returns_singleton():
    chat_agent.get_message_histories.cache_clear()
    try:
        assert chat_agent.get_message_histories.cache_info().maxsize == 1

        first = chat_agent.get_message_histories()
        second = chat_agent.get_message_histories()

        assert first is second

        first["session-1"] = []
        assert second == {"session-1": []}
    finally:
        chat_agent.get_message_histories.cache_clear()


def test_clear_history_removes_only_requested_session():
    histories = chat_agent.get_message_histories()
    histories["session-1"] = ["message-1"]
    histories["session-2"] = ["message-2"]

    chat_agent.clear_history("session-1")

    assert histories == {"session-2": ["message-2"]}


def test_get_agent_response_raises_when_chat_is_not_configured():
    with pytest.raises(RuntimeError, match="Chat is not configured"):
        asyncio.run(chat_agent.get_agent_response("hello", session_id="session-1"))


def test_get_agent_response_parses_charts_and_updates_message_history(monkeypatch):
    existing_history = ["old-message"]
    chat_agent.get_message_histories()["session-1"] = existing_history
    result = FakeResult(
        'Here is a chart [CHART_DATA]{"type": "bar", "x": "age"}[/CHART_DATA]',
        ["new-message"],
    )
    agent = FakeAgent(result)

    monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_agent, "_create_agent_for_session", lambda session_id: agent)

    response = asyncio.run(
        chat_agent.get_agent_response("show me age", session_id="session-1")
    )

    assert response == {
        "text": "Here is a chart",
        "charts": [{"type": "bar", "x": "age"}],
    }
    assert agent.run_calls == [
        {
            "message": "show me age",
            "message_history": existing_history,
            "message_history_snapshot": ["old-message"],
        }
    ]
    assert chat_agent.get_message_histories()["session-1"] == [
        "old-message",
        "new-message",
    ]


def test_get_agent_response_falls_back_when_mcp_context_fails(monkeypatch):
    result = FakeResult("plain response", ["new-message"])
    agent = FakeAgent(result, enter_error=RuntimeError("mcp unavailable"))

    monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_agent, "_create_agent_for_session", lambda session_id: agent)

    response = asyncio.run(
        chat_agent.get_agent_response("hello", session_id="session-1")
    )

    assert response == {"text": "plain response", "charts": []}
    assert agent.run_calls == [
        {
            "message": "hello",
            "message_history": ["new-message"],
            "message_history_snapshot": [],
        }
    ]
    assert chat_agent.get_message_histories()["session-1"] == ["new-message"]
