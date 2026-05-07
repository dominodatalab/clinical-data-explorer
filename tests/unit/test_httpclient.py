import backend.services.httpclient as httpclient


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_get_returns_json_and_applies_defaults(monkeypatch):
    captured = []

    def fake_requests_get(*args, **kwargs):
        captured.append((args, kwargs))
        return _FakeResponse(200, payload={"ok": True})

    monkeypatch.setattr(httpclient.requests, "get", fake_requests_get)

    response = httpclient.get("https://domino.example/api/test", headers={"Authorization": "Bearer token"})

    assert response == {"ok": True}
    assert captured == [
        (
            ("https://domino.example/api/test",),
            {
                "headers": {"Authorization": "Bearer token"},
                "timeout": 120,
                "stream": True,
            },
        )
    ]


def test_get_returns_raw_response_when_is_json_false(monkeypatch):
    fake_response = _FakeResponse(200, payload={"ok": True})
    monkeypatch.setattr(httpclient.requests, "get", lambda *args, **kwargs: fake_response)

    response = httpclient.get("https://domino.example/api/raw", is_json=False)

    assert response is fake_response


def test_get_raises_access_denied_error(monkeypatch):
    monkeypatch.setattr(httpclient.requests, "get", lambda *args, **kwargs: _FakeResponse(403, text="forbidden"))

    try:
        httpclient.get("https://domino.example/api/forbidden")
        assert False, "expected HTTPClientError"
    except httpclient.HTTPClientError as exc:
        assert exc.status_code == 403
        assert exc.text == "Access denied. Your session may have expired."


def test_get_raises_error_for_other_non_success_status(monkeypatch):
    monkeypatch.setattr(httpclient.requests, "get", lambda *args, **kwargs: _FakeResponse(500, text="server blew up"))

    try:
        httpclient.get("https://domino.example/api/fail")
        assert False, "expected HTTPClientError"
    except httpclient.HTTPClientError as exc:
        assert exc.status_code == 500
        assert exc.text == "server blew up"
