from fastapi import HTTPException
from fastapi.testclient import TestClient

import mcp_server.session as session_module
from mcp_server.app import create_app


def test_unhandled_exception_returns_json_500(monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "test-error-middleware"})
    session_module._current_user_id.set(None)
    app = create_app()

    @app.get("/test-unhandled-error")
    async def test_unhandled_error():
        raise RuntimeError("boom")

    client = TestClient(app)

    response = client.get("/test-unhandled-error")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "boom"}


def test_http_exception_preserves_status_and_detail(monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "test-http-exception"})
    session_module._current_user_id.set(None)
    app = create_app()

    @app.get("/test-http-exception")
    async def test_http_exception():
        raise HTTPException(status_code=418, detail="short and stout")

    client = TestClient(app)

    response = client.get("/test-http-exception")

    assert response.status_code == 418
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "short and stout"}
