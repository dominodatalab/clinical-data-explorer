import logging

import requests

import backend.services.dataset_reload_context as dataset_reload_context


class _FakeMcpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_get_reload_context_logs_mcp_request_exception(monkeypatch, caplog):
    def raise_request_exception(path, session_id=None):
        raise requests.exceptions.ConnectionError("mcp unavailable")

    monkeypatch.setattr(dataset_reload_context, "mcp_get", raise_request_exception)

    with caplog.at_level(logging.WARNING, logger=dataset_reload_context.__name__):
        context = dataset_reload_context.get_reload_context("sid-exception")

    assert context is None
    assert "Could not get dataset reload context for session sid-exception: mcp unavailable" in caplog.text


def test_get_reload_context_logs_mcp_error_response(monkeypatch, caplog):
    monkeypatch.setattr(
        dataset_reload_context,
        "mcp_get",
        lambda path, session_id=None: _FakeMcpResponse(503, {"detail": "unavailable"}),
    )

    with caplog.at_level(logging.WARNING, logger=dataset_reload_context.__name__):
        context = dataset_reload_context.get_reload_context("sid-error")

    assert context is None
    assert "Could not get dataset reload context for session sid-error: MCP returned HTTP 503" in caplog.text


def test_get_reload_context_logs_missing_load_body(monkeypatch, caplog):
    monkeypatch.setattr(
        dataset_reload_context,
        "mcp_get",
        lambda path, session_id=None: _FakeMcpResponse(200, {"dataset": None, "reload_context": None}),
    )

    with caplog.at_level(logging.INFO, logger=dataset_reload_context.__name__):
        context = dataset_reload_context.get_reload_context("sid-missing-context")

    assert context is None
    assert "no load body found" in caplog.text
