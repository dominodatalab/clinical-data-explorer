from flask import Flask

from backend import config
import backend.session as backend_session


def _create_test_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


def test_mcp_get_applies_default_timeout_and_authorization_header(monkeypatch):
    app = _create_test_app()
    captured = []
    response = object()

    def fake_get(*args, **kwargs):
        captured.append((args, kwargs))
        return response

    monkeypatch.setattr(config, "MCP_SERVER_URL", "http://mcp.example")
    monkeypatch.setattr(config, "MCP_REQUEST_TIMEOUT_SECONDS", 42)
    monkeypatch.setattr(backend_session.requests, "get", fake_get)
    monkeypatch.setattr(backend_session, "get_current_user", lambda: {"id": "user-1"})
    monkeypatch.setattr(backend_session, "get_passthrough_token", lambda: "token-1")

    with app.test_request_context("/"):
        result = backend_session.mcp_get("/dataset/data", params={"limit": "10"})

    assert result is response
    assert captured == [
        (
            ("http://mcp.example/dataset/data",),
            {
                "headers": {"Authorization": "Bearer token-1"},
                "params": {"limit": "10"},
                "timeout": 42,
            },
        )
    ]


def test_mcp_post_allows_explicit_timeout_override(monkeypatch):
    captured = []
    response = object()

    def fake_post(*args, **kwargs):
        captured.append((args, kwargs))
        return response

    monkeypatch.setattr(config, "MCP_SERVER_URL", "http://mcp.example")
    monkeypatch.setattr(config, "MCP_REQUEST_TIMEOUT_SECONDS", 42)
    monkeypatch.setattr(backend_session.requests, "post", fake_post)

    result = backend_session.mcp_post(
        "/table/data",
        session_id="sid-override",
        json={"page": 1},
        headers={"X-Trace-Id": "trace-1"},
        timeout=7,
    )

    assert result is response
    assert captured == [
        (
            ("http://mcp.example/table/data",),
            {
                "headers": {"X-Trace-Id": "trace-1"},
                "json": {"page": 1},
                "timeout": 7,
            },
        )
    ]
