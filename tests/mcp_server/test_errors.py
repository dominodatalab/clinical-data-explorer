"""`detail` stays byte-identical for REST clients; `model_safe_detail` is opt-in."""
import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import mcp_server.routes.charts as charts_routes
import mcp_server.session as session_module
from mcp_server.app import create_app
from mcp_server.errors import ToolError, error_body


def test_error_body_includes_model_safe_detail_only_when_set():
    assert error_body(HTTPException(400, "plain")) == {"detail": "plain"}
    assert error_body(ToolError(400, "no safe text")) == {"detail": "no safe text"}
    assert error_body(ToolError(400, "secret", model_safe_detail="safe")) == {
        "detail": "secret",
        "model_safe_detail": "safe",
    }
    assert error_body(ToolError.safe(404, "Column 'X' not found")) == {
        "detail": "Column 'X' not found",
        "model_safe_detail": "Column 'X' not found",
    }


def test_tool_error_handler_renders_both_fields(monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "test-tool-error"})
    session_module._current_user_id.set(None)
    app = create_app()

    @app.get("/boom")
    async def boom():
        raise ToolError(418, "short", headers={"X-Test": "1"}, model_safe_detail="stout")

    response = TestClient(app).get("/boom")

    assert response.status_code == 418
    assert response.headers["x-test"] == "1"
    assert response.json() == {"detail": "short", "model_safe_detail": "stout"}


def test_plain_http_exception_has_no_model_safe_detail(monkeypatch):
    monkeypatch.setattr(session_module, "get_current_user", lambda: {"id": "test-plain"})
    session_module._current_user_id.set(None)
    app = create_app()

    @app.get("/plain")
    async def plain():
        raise HTTPException(status_code=400, detail="could contain data")

    assert TestClient(app).get("/plain").json() == {"detail": "could contain data"}


# Argument-echo errors: detail unchanged, and safe to show a model.

@pytest.mark.parametrize("method, path, payload, status, detail", [
    ("get", "/feature/histogram/nope", None, 404, "Feature 'nope' not found"),
    ("get", "/feature/histogram/treatment", None, 400, "Feature 'treatment' is not numeric"),
    ("get", "/table/column_stats/nope", None, 404, "Column 'nope' not found"),
    ("post", "/chart/histogram", {"column": "nope"}, 404, "Column 'nope' not found"),
    ("get", "/dataset/head?rows=0", None, 400, "Rows parameter must be positive"),
])
def test_argument_echo_errors_are_model_safe(mcp_client, method, path, payload, status, detail):
    response = mcp_client.get(path) if method == "get" else mcp_client.post(path, json=payload)

    assert response.status_code == status
    assert response.json() == {"detail": detail, "model_safe_detail": detail}


def test_no_dataset_loaded_is_model_safe(_mcp_app):
    client = TestClient(_mcp_app)

    response = client.get("/dataset/info")

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "No dataset loaded. Please load a dataset first using /dataset/load"
    assert body["model_safe_detail"] == body["detail"]


def test_unknown_expression_column_names_are_model_safe(mcp_client):
    response = mcp_client.post(
        "/table/expression_filter",
        json={"expression": "agee > 5", "syntax": "python"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"].startswith("Unknown column(s): agee")
    assert body["model_safe_detail"] == body["detail"]


# Leaky sites: detail unchanged, but the model-safe text carries no exception text.

def test_date_parse_error_keeps_detail_but_hides_exception_text_from_model(mcp_client, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("Unknown datetime string format, unable to parse: Placebo")

    monkeypatch.setattr(charts_routes.pd, "to_datetime", fail)

    response = mcp_client.post(
        "/chart/time_series",
        json={"date_column": "treatment", "value_column": "age"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Could not parse dates: Unknown datetime string format, unable to parse: Placebo"
    assert body["model_safe_detail"] == "Could not parse dates in column 'treatment'"
    assert "Placebo" not in body["model_safe_detail"]


def test_invalid_expression_keeps_detail_but_hides_exception_text_from_model(mcp_client):
    response = mcp_client.post(
        "/table/expression_filter",
        json={"expression": "age > 'Placebo'", "syntax": "python"},
    )

    assert response.status_code == 400
    body = response.json()
    assert "Placebo" in body["detail"]
    assert "Placebo" not in body["model_safe_detail"]
    assert body["model_safe_detail"]


def test_expression_application_error_hides_exception_text_from_model(mcp_client, monkeypatch):
    import mcp_server.routes.filters as filters_routes

    def fail(df, expression, syntax):
        raise RuntimeError("cannot compare 'Placebo' with 3")

    monkeypatch.setattr(filters_routes, "apply_expression_filter", fail)

    response = mcp_client.post(
        "/table/expression_filter",
        json={"expression": "age > 3", "syntax": "python"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Error applying expression: cannot compare 'Placebo' with 3"
    assert "Placebo" not in body["model_safe_detail"]


def test_validation_errors_are_untouched(mcp_client):
    response = mcp_client.post("/chart/histogram", json={})

    assert response.status_code == 422
    assert "model_safe_detail" not in response.json()
