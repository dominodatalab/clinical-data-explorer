"""Gated smoke test against a real Domino LLM Gateway.

Marked `external` and skipped unless both Gateway env vars are set, so it never
runs in CI by default:

    DOMINO_LLM_GATEWAY_URL=https://<deploy>/apps/<app-id> \\
    DOMINO_LLM_GATEWAY_MODEL=gpt-5.4-nano \\
    pytest -m external tests/contract/test_chat_gateway_external.py

This catches the failures unit tests structurally cannot see: a wrong base URL,
an unregistered model alias, or an access token the Gateway rejects.
"""

import os

import pytest
from flask import Flask

import backend.routes.chat as chat_routes
import chat_agent

pytestmark = pytest.mark.external

_GATEWAY_ENV_SET = bool(
    os.environ.get("DOMINO_LLM_GATEWAY_URL")
    and os.environ.get("DOMINO_LLM_GATEWAY_MODEL")
)


@pytest.mark.skipif(
    not _GATEWAY_ENV_SET,
    reason="DOMINO_LLM_GATEWAY_URL and DOMINO_LLM_GATEWAY_MODEL must both be set",
)
def test_chat_round_trip_through_the_domino_gateway(monkeypatch):
    # The model client is a process-wide singleton; drop it so this test builds
    # one from the live environment rather than reusing a cached one.
    chat_agent._llm_model = None

    # get_session_id() calls the Domino users API, which is not what this test
    # is probing — pin it so a failure here can only come from the Gateway.
    monkeypatch.setattr(chat_routes, "get_session_id", lambda: "gateway-smoke-test")

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    app.register_blueprint(chat_routes.bp)

    status = app.test_client().get("/chat/status").get_json()
    assert status["provider"] == "domino_gateway", status
    assert status["configured"] is True, status

    response = app.test_client().post(
        "/chat", json={"message": "Reply with the single word: pong"}
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["response"].strip(), "Gateway returned an empty response"
