"""Unit tests for Domino LLM Gateway provider selection and auth.

The contract owned here is "given env X, the built config has property Y" plus
"the auth flow picks token Z". The LLM SDK itself is not under test.

The autouse fixture clears every LLM env var and drops the cached model
singleton: without it, a stray `export LLM_API_KEY` in a developer's shell
silently flips cases and the `_llm_model` cached by the first test pins the
provider for the rest of the session.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from flask import Flask

import backend.routes.chat as chat_routes
import chat_agent

_LLM_ENV_VARS = (
    "DOMINO_LLM_GATEWAY_URL",
    "DOMINO_LLM_GATEWAY_MODEL",
    "DOMINO_LLM_GATEWAY_TOKEN_URL",
    "API_KEY_OVERRIDE",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "LLM_MODEL",
)

TOKEN_URL = "http://localhost:8899/access-token"


@pytest.fixture(autouse=True)
def reset_llm_state(monkeypatch):
    for env_var in _LLM_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    chat_agent._llm_model = None
    yield
    chat_agent._llm_model = None


def _set_gateway_env(monkeypatch, url="https://deploy.example/apps/app-1", model="gpt-5.4-nano"):
    monkeypatch.setenv("DOMINO_LLM_GATEWAY_URL", url)
    monkeypatch.setenv("DOMINO_LLM_GATEWAY_MODEL", model)


# ===== PROVIDER SELECTION =====

def test_gateway_env_selects_gateway_provider_and_appends_v1(monkeypatch):
    _set_gateway_env(monkeypatch)

    assert chat_agent.get_llm_config() == (
        "domino_gateway",
        "https://deploy.example/apps/app-1/v1",
        None,
        "gpt-5.4-nano",
    )
    assert chat_agent.is_chat_configured() is True


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://deploy.example/apps/app-1/v1",
        "https://deploy.example/apps/app-1/v1/",
        "https://deploy.example/apps/app-1/",
    ],
)
def test_gateway_base_url_is_normalized_to_a_single_v1_suffix(monkeypatch, raw_url):
    _set_gateway_env(monkeypatch, url=raw_url)

    _provider, base_url, _api_key, _model = chat_agent.get_llm_config()
    assert base_url == "https://deploy.example/apps/app-1/v1"


def test_gateway_outranks_a_stale_llm_api_key(monkeypatch):
    """Operators routinely leave old keys behind in project settings, so the
    Gateway wins rather than silently sending traffic to the stale provider."""
    _set_gateway_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-stale")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")

    provider, _base_url, api_key, model = chat_agent.get_llm_config()

    assert provider == "domino_gateway"
    assert api_key is None
    assert model == "gpt-5.4-nano"


def test_half_configured_gateway_falls_through_to_the_next_provider(monkeypatch):
    """Only one of the two Gateway vars set means "Gateway not requested" —
    there is deliberately no half-configured error state."""
    monkeypatch.setenv("DOMINO_LLM_GATEWAY_URL", "https://deploy.example/apps/app-1")

    provider, _base_url, _api_key, _model = chat_agent.get_llm_config()

    assert provider == "openai"
    assert chat_agent.is_chat_configured() is False


def test_local_base_url_selects_ollama(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3")

    status = chat_agent.get_chat_status()

    assert status["provider"] == "ollama"
    assert status["is_local"] is True
    assert status["configured"] is True
    assert status["missing"] == []


def test_chat_status_for_configured_gateway(monkeypatch):
    _set_gateway_env(monkeypatch)

    assert chat_agent.get_chat_status() == {
        "configured": True,
        "provider": "domino_gateway",
        "base_url": "https://deploy.example/apps/app-1/v1",
        "model": "gpt-5.4-nano",
        "is_local": False,
        "missing": [],
    }


def test_chat_status_with_nothing_configured_asks_for_an_api_key():
    status = chat_agent.get_chat_status()

    assert status["configured"] is False
    assert status["provider"] == "openai"
    assert status["missing"] == ["LLM_API_KEY"]


# ===== DominoAccessTokenAuth =====

def _make_request():
    return httpx.Request("POST", "https://gateway.example/v1/chat/completions")


def _fake_token_response(text):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_token_auth_refetches_on_every_call():
    """No caching is the whole point: the sidecar token expires within minutes,
    so a caching regression here passes tests and then fails in production."""
    auth = chat_agent.DominoAccessTokenAuth(TOKEN_URL)
    fake_resp = _fake_token_response("token-abc")

    with patch("chat_agent.requests.get", return_value=fake_resp) as mock_get:
        first = next(auth.auth_flow(_make_request()))
        assert first.headers["Authorization"] == "Bearer token-abc"

        fake_resp.text = "token-xyz"  # token rotated behind our back
        second = next(auth.auth_flow(_make_request()))

        assert second.headers["Authorization"] == "Bearer token-xyz"
        assert mock_get.call_count == 2


def test_token_auth_strips_a_bearer_prefix_from_the_sidecar():
    """Some sidecars hand back the whole header value; the prefix must not double."""
    auth = chat_agent.DominoAccessTokenAuth(TOKEN_URL)

    with patch("chat_agent.requests.get", return_value=_fake_token_response("Bearer raw-token\n")):
        request = next(auth.auth_flow(_make_request()))

    assert request.headers["Authorization"] == "Bearer raw-token"


def test_api_key_override_skips_the_sidecar(monkeypatch):
    monkeypatch.setenv("API_KEY_OVERRIDE", "override-key")
    auth = chat_agent.DominoAccessTokenAuth(TOKEN_URL)

    with patch("chat_agent.requests.get") as mock_get:
        request = next(auth.auth_flow(_make_request()))

    assert request.headers["Authorization"] == "Bearer override-key"
    mock_get.assert_not_called()


def test_passthrough_token_beats_sidecar():
    """The audit-attribution guarantee: when the visiting user's JWT is present
    the Gateway call must carry it, not the app owner's sidecar token."""
    auth = chat_agent.DominoAccessTokenAuth(TOKEN_URL)

    handle = chat_agent.set_gateway_passthrough_token("visitor-jwt")
    try:
        with patch("chat_agent.requests.get") as mock_get:
            request = next(auth.auth_flow(_make_request()))

        assert request.headers["Authorization"] == "Bearer visitor-jwt"
        mock_get.assert_not_called()
    finally:
        chat_agent.reset_gateway_passthrough_token(handle)


def test_explicitly_none_passthrough_token_falls_back_to_the_sidecar():
    auth = chat_agent.DominoAccessTokenAuth(TOKEN_URL)

    handle = chat_agent.set_gateway_passthrough_token(None)
    try:
        with patch("chat_agent.requests.get", return_value=_fake_token_response("sidecar-token")):
            request = next(auth.auth_flow(_make_request()))

        assert request.headers["Authorization"] == "Bearer sidecar-token"
    finally:
        chat_agent.reset_gateway_passthrough_token(handle)


def test_api_key_override_beats_the_passthrough_token(monkeypatch):
    monkeypatch.setenv("API_KEY_OVERRIDE", "override-key")
    auth = chat_agent.DominoAccessTokenAuth(TOKEN_URL)

    handle = chat_agent.set_gateway_passthrough_token("visitor-jwt")
    try:
        request = next(auth.auth_flow(_make_request()))
    finally:
        chat_agent.reset_gateway_passthrough_token(handle)

    assert request.headers["Authorization"] == "Bearer override-key"


# ===== MODEL / AGENT CONSTRUCTION =====

def test_gateway_model_gets_an_http_client_carrying_the_token_auth(monkeypatch):
    _set_gateway_env(monkeypatch)
    monkeypatch.setenv("DOMINO_LLM_GATEWAY_TOKEN_URL", "http://token.test/access-token")
    captured = {}

    def capturing_provider(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(chat_agent, "OpenAIProvider", capturing_provider)
    monkeypatch.setattr(chat_agent, "OpenAIChatModel", lambda model_name, provider: (model_name, provider))

    chat_agent._get_llm_model()

    assert captured["base_url"] == "https://deploy.example/apps/app-1/v1"
    # The OpenAI SDK refuses an empty api_key even though auth_flow overwrites
    # the Authorization header, so a placeholder is passed instead.
    assert captured["api_key"] == "domino-gateway"
    auth = captured["http_client"].auth
    assert isinstance(auth, chat_agent.DominoAccessTokenAuth)
    assert auth._token_url == "http://token.test/access-token"


def test_non_gateway_model_gets_no_custom_http_client(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    captured = {}

    def capturing_provider(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(chat_agent, "OpenAIProvider", capturing_provider)
    monkeypatch.setattr(chat_agent, "OpenAIChatModel", lambda model_name, provider: (model_name, provider))

    chat_agent._get_llm_model()

    assert captured == {"base_url": "https://api.openai.com/v1", "api_key": "sk-test"}


def test_agents_carry_the_max_output_token_cap(monkeypatch):
    captured = []

    monkeypatch.setattr(chat_agent, "MCPServerSSE", lambda url, headers: object())
    monkeypatch.setattr(chat_agent, "_get_llm_model", lambda: object())
    monkeypatch.setattr(chat_agent, "Agent", lambda *args, **kwargs: captured.append(kwargs))

    chat_agent._create_agent_for_session("user-1")
    chat_agent._create_agent_without_mcp()

    assert [kwargs["model_settings"]["max_tokens"] for kwargs in captured] == [
        chat_agent.LLM_MAX_OUTPUT_TOKENS,
        chat_agent.LLM_MAX_OUTPUT_TOKENS,
    ]


# ===== ROUTE WIRING =====

def _chat_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    app.register_blueprint(chat_routes.bp)
    return app


def test_chat_route_sets_and_resets_the_passthrough_token(monkeypatch):
    """The `finally` reset is not optional: a leaked visitor JWT would fail
    *open* on a later request, attributing it to the wrong user."""
    seen = []

    monkeypatch.setattr(chat_routes, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_routes, "get_passthrough_token", lambda: "visitor-jwt")
    monkeypatch.setattr(chat_routes, "get_session_id", lambda: "user-1")

    async def fake_agent_response(message, session_id=None, authorization_header=None):
        seen.append(chat_agent._passthrough_token_var.get())
        return {"text": "hi", "charts": []}

    monkeypatch.setattr(chat_routes, "get_agent_response", fake_agent_response)

    response = _chat_app().test_client().post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert seen == ["visitor-jwt"]
    assert chat_agent._passthrough_token_var.get() is None


def test_chat_route_resets_the_passthrough_token_after_a_failure(monkeypatch):
    monkeypatch.setattr(chat_routes, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_routes, "get_passthrough_token", lambda: "visitor-jwt")
    monkeypatch.setattr(chat_routes, "get_session_id", lambda: "user-1")

    async def failing_agent_response(message, session_id=None, authorization_header=None):
        raise ValueError("boom")

    monkeypatch.setattr(chat_routes, "get_agent_response", failing_agent_response)

    response = _chat_app().test_client().post("/chat", json={"message": "hello"})

    assert response.status_code == 500
    assert chat_agent._passthrough_token_var.get() is None


def test_chat_route_503_lists_all_three_configuration_paths(monkeypatch):
    monkeypatch.setattr(chat_routes, "is_chat_configured", lambda: False)

    response = _chat_app().test_client().post("/chat", json={"message": "hello"})

    assert response.status_code == 503
    detail = response.get_json()["error_detail"]
    assert "DOMINO_LLM_GATEWAY_URL" in detail
    assert "LLM_API_KEY" in detail
    assert "LLM_BASE_URL" in detail
