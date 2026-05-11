from flask import Flask, session

from backend import config
import backend.session as backend_session


def _create_test_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


def test_mcp_get_applies_default_timeout_and_session_header(monkeypatch):
    app = _create_test_app()
    captured = []
    response = object()

    def fake_get(*args, **kwargs):
        captured.append((args, kwargs))
        return response

    monkeypatch.setattr(config, "MCP_SERVER_URL", "http://mcp.example")
    monkeypatch.setattr(config, "MCP_REQUEST_TIMEOUT_SECONDS", 42)
    monkeypatch.setattr(backend_session.requests, "get", fake_get)

    with app.test_request_context("/"):
        session["sid"] = "sid-123"
        result = backend_session.mcp_get("/dataset/data", params={"limit": "10"})

    assert result is response
    assert captured == [
        (
            ("http://mcp.example/dataset/data",),
            {
                "headers": {"X-Session-Id": "sid-123"},
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
                "headers": {"X-Trace-Id": "trace-1", "X-Session-Id": "sid-override"},
                "json": {"page": 1},
                "timeout": 7,
            },
        )
    ]
