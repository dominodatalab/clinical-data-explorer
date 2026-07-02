from flask import Flask

from backend import config
import backend.session as backend_session
import pytest


def _create_test_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


@pytest.fixture(autouse=True)
def clear_current_user_id():
    backend_session._current_user_id.set(None)
    yield
    backend_session._current_user_id.set(None)


def test_refresh_current_user_id_updates_cache_for_new_request(monkeypatch):
    backend_session._current_user_id.set("stale-user")
    calls = []
    monkeypatch.setattr(
        backend_session,
        "get_current_user",
        lambda: calls.append("get_current_user") or {"id": "fresh-user"},
    )

    assert backend_session.refresh_current_user_id() == "fresh-user"
    assert backend_session.get_session_id() == "fresh-user"
    assert calls == ["get_current_user"]


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

    with app.test_request_context("/", headers={"Authorization": "Bearer token-123"}):
        result = backend_session.mcp_get("/dataset/data", params={"limit": "10"})

    assert result is response
    assert captured == [
        (
                ("http://mcp.example/dataset/data",),
                {
                    "headers": {"Authorization": "Bearer token-123"},
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

    with _create_test_app().test_request_context("/", headers={"Authorization": "Bearer token-override"}):
        result = backend_session.mcp_post(
            "/table/data",
            session_id="ignored-user-id",
            json={"page": 1},
            headers={"X-Trace-Id": "trace-1"},
            timeout=7,
        )

    assert result is response
    assert captured == [
        (
                ("http://mcp.example/table/data",),
                {
                    "headers": {"X-Trace-Id": "trace-1", "Authorization": "Bearer token-override"},
                    "json": {"page": 1},
                    "timeout": 7,
                },
        )
    ]
