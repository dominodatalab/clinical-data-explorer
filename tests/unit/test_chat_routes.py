from flask import Flask, jsonify, make_response

import backend.routes.chat as chat_routes
import backend.services.dataframe_reload as dataframe_reload


class _FakeMcpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _create_test_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(chat_routes.bp)
    return app


def test_chat_reloads_expired_dataframe_before_calling_agent(monkeypatch):
    app = _create_test_app()
    metadata_calls = []
    reload_calls = []
    agent_calls = []

    monkeypatch.setattr(chat_routes, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_routes, "get_session_id", lambda: "sid-chat")

    def fake_mcp_get(path):
        metadata_calls.append(path)
        if len(metadata_calls) == 1:
            return _FakeMcpResponse(400, {"detail": {"error": "No dataset loaded."}})
        return _FakeMcpResponse(200, {"metadata": {"available": True}})

    async def fake_get_agent_response(message, session_id="default"):
        agent_calls.append((message, session_id))
        return {"text": "loaded answer", "charts": []}

    monkeypatch.setattr(chat_routes, "mcp_get", fake_mcp_get)
    monkeypatch.setattr(
        dataframe_reload,
        "try_reload_expired_dataframe",
        lambda session_id: reload_calls.append(session_id) or (True, None),
    )
    monkeypatch.setattr(chat_routes, "get_agent_response", fake_get_agent_response)

    with app.test_client() as client:
        response = client.post("/chat", json={"message": "summarize the data"})

    assert response.status_code == 200
    assert response.get_json() == {"response": "loaded answer", "charts": []}
    assert metadata_calls == ["/dataset/metadata", "/dataset/metadata"]
    assert reload_calls == ["sid-chat"]
    assert agent_calls == [("summarize the data", "sid-chat")]


def test_chat_reports_reload_context_error_without_calling_agent(monkeypatch):
    app = _create_test_app()
    agent_calls = []

    monkeypatch.setattr(chat_routes, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_routes, "get_session_id", lambda: "sid-chat-missing-context")
    monkeypatch.setattr(
        chat_routes,
        "mcp_get",
        lambda path: _FakeMcpResponse(400, {"detail": {"error": "No dataset loaded."}}),
    )
    monkeypatch.setattr(
        dataframe_reload,
        "try_reload_expired_dataframe",
        lambda session_id: (
            False,
            make_response(jsonify({"error": "Your data expired and couldn't be reloaded. Please select the file again."}), 400),
        ),
    )

    async def fake_get_agent_response(message, session_id="default"):
        agent_calls.append((message, session_id))
        return {"text": "should not run", "charts": []}

    monkeypatch.setattr(chat_routes, "get_agent_response", fake_get_agent_response)

    with app.test_client() as client:
        response = client.post("/chat", json={"message": "summarize the data"})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Your data expired and couldn't be reloaded. Please select the file again.",
    }
    assert agent_calls == []
