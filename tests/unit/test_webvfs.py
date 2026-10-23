import pytest
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from backend.services.webvfs import get_webvfs_base_url


def _request_webvfs_base_url_through_proxy(headers):
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    @app.get("/")
    def resolve_webvfs_base_url():
        try:
            return {"base_url": get_webvfs_base_url()}
        except RuntimeError as exc:
            return {"error": str(exc)}, 400

    return app.test_client().get("/", headers=headers)


def _resolve_webvfs_base_url_through_proxy(headers):
    response = _request_webvfs_base_url_through_proxy(headers)

    assert response.status_code == 200
    return response.get_json()["base_url"]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("http://127.0.0.1:8763", "http://127.0.0.1:8763"),
        (
            "http://data-webvfs-service.custom-compute/",
            "http://data-webvfs-service.custom-compute",
        ),
        ("webvfs.internal:8080", "http://webvfs.internal:8080"),
        ("  https://webvfs.example  ", "https://webvfs.example"),
        ("HTTPS://webvfs.example", "https://webvfs.example"),
        ("https://webvfs.example/webvfs", "https://webvfs.example"),
        ("https://webvfs.example/path?key=value#fragment", "https://webvfs.example"),
    ],
)
def test_get_webvfs_base_url_returns_only_the_configured_origin(monkeypatch, host, expected):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)

    assert get_webvfs_base_url() == expected


def test_get_webvfs_base_url_ignores_domino_external_url(monkeypatch):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", "http://127.0.0.1:8763")
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://external.example")

    assert _resolve_webvfs_base_url_through_proxy(
        {
            "X-Forwarded-Host": "apps.domino.example",
            "X-Forwarded-Proto": "https",
        }
    ) == "http://127.0.0.1:8763"


@pytest.mark.parametrize("host", [None, "", "   "])
def test_get_webvfs_base_url_falls_back_to_forwarded_origin_for_domino_6_3_1(monkeypatch, host):
    if host is None:
        monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    else:
        monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)
    monkeypatch.delenv("DOMINO_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("VSCODE_PROXY_URI", raising=False)

    assert _resolve_webvfs_base_url_through_proxy(
        {
            "X-Forwarded-Host": "untrusted.example, domino.example",
            "X-Forwarded-Proto": "http, https",
        }
    ) == "https://domino.example"


def test_get_webvfs_base_url_raises_when_no_host_or_request_origin_is_available(monkeypatch):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://external.example")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/123")

    with pytest.raises(RuntimeError, match="Domino WebVFS host is not configured"):
        get_webvfs_base_url()


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "untrusted.example"},
        {"X-Forwarded-Host": "domino.example"},
        {"X-Forwarded-Proto": "https"},
        {
            "Host": "untrusted.example",
            "X-Forwarded-Host": " ",
            "X-Forwarded-Proto": " ",
        },
        {
            "Host": "untrusted.example",
            "X-Forwarded-Host": ",",
            "X-Forwarded-Proto": ",",
        },
    ],
)
def test_get_webvfs_base_url_requires_forwarded_host_and_proto(monkeypatch, headers):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)

    response = _request_webvfs_base_url_through_proxy(headers)

    assert response.status_code == 400
    assert "no trusted forwarded request origin" in response.get_json()["error"]


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forwarded-Host": "domino.example", "X-Forwarded-Proto": "ftp"},
        {"X-Forwarded-Host": "user@domino.example", "X-Forwarded-Proto": "https"},
    ],
)
def test_get_webvfs_base_url_rejects_invalid_request_origins(monkeypatch, headers):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)

    response = _request_webvfs_base_url_through_proxy(headers)

    assert response.status_code == 400
    assert "request origin" in response.get_json()["error"]


@pytest.mark.parametrize(
    "host",
    [
        "/webvfs",
        "ftp://webvfs.example",
        "http://webvfs.example:bad",
        "https://user@webvfs.example",
        "https://user:password@webvfs.example",
    ],
)
def test_get_webvfs_base_url_rejects_invalid_hosts(monkeypatch, host):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)

    with Flask(__name__).test_request_context(base_url="https://domino.example"):
        with pytest.raises(RuntimeError, match=r"valid HTTP\(S\) host"):
            get_webvfs_base_url()
