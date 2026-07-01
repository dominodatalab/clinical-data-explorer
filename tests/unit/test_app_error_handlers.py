import backend.app as backend_app


def _create_error_handler_test_app(monkeypatch):
    monkeypatch.setattr(backend_app, "initialize_session_id", lambda: "user-1")
    app = backend_app.create_app()
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/test-unhandled-error")
    def test_unhandled_error():
        raise RuntimeError("boom")

    return app


def test_unhandled_exception_returns_json_500(monkeypatch):
    app = _create_error_handler_test_app(monkeypatch)

    with app.test_client() as client:
        response = client.get("/test-unhandled-error")

    assert response.status_code == 500
    assert response.content_type == "application/json"
    assert response.get_json() == {
        "code": 500,
        "name": "Internal Server Error",
        "description": "boom",
    }


def test_http_exception_handler_still_returns_original_status(monkeypatch):
    app = _create_error_handler_test_app(monkeypatch)

    with app.test_client() as client:
        response = client.get("/not-found")

    assert response.status_code == 404
    assert response.content_type == "application/json"
    assert response.get_json()["code"] == 404
