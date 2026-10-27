import pytest
from flask import Flask

import backend.services.webvfs as webvfs
from backend import config
from backend.services.webvfs import get_webvfs_base_url


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (None, None),
        ("", ""),
        ("   ", ""),
        ("  https://webvfs.example  ", "https://webvfs.example"),
    ],
)
def test_get_domino_webvfs_host_normalizes_whitespace(monkeypatch, host, expected):
    if host is None:
        monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    else:
        monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)

    assert config.get_domino_webvfs_host() == expected


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


def test_get_webvfs_base_url_prefers_configured_host(monkeypatch):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", "http://127.0.0.1:8763")

    def unexpected_external_url_lookup():
        pytest.fail("external URL fallback should not run when DOMINO_WEBVFS_HOST is set")

    monkeypatch.setattr(webvfs, "get_domino_external_url", unexpected_external_url_lookup)

    assert get_webvfs_base_url() == "http://127.0.0.1:8763"


@pytest.mark.parametrize("host", [None, "", "   "])
def test_get_webvfs_base_url_falls_back_to_domino_url(monkeypatch, host):
    if host is None:
        monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    else:
        monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)
    monkeypatch.setenv("DOMINO_URL", "https://domino.example")

    assert get_webvfs_base_url() == "https://domino.example"


def test_get_webvfs_base_url_uses_domino_url_origin_and_ignores_app_headers(monkeypatch):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.setenv("DOMINO_URL", "  https://domino.example/  ")
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://external.example")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/123")

    with Flask(__name__).test_request_context(
        headers={
            "Host": "apps.domino.example",
            "X-Forwarded-Host": "apps.domino.example",
            "X-Forwarded-Proto": "https",
        }
    ):
        assert get_webvfs_base_url() == "https://domino.example"


@pytest.mark.parametrize("domino_url", [None, "", "   "])
def test_get_webvfs_base_url_raises_when_no_host_or_domino_url_is_available(
    monkeypatch, domino_url
):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    if domino_url is None:
        monkeypatch.delenv("DOMINO_URL", raising=False)
    else:
        monkeypatch.setenv("DOMINO_URL", domino_url)
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://external.example")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/123")

    with Flask(__name__).test_request_context(
        headers={
            "Host": "apps.domino.example",
            "X-Forwarded-Host": "domino.example",
            "X-Forwarded-Proto": "https",
        }
    ):
        with pytest.raises(RuntimeError, match="no external URL is available"):
            get_webvfs_base_url()


@pytest.mark.parametrize(
    "domino_url",
    [
        "ftp://domino.example",
        "https://user@domino.example",
        "https://",
        "https://domino.example:bad",
    ],
)
def test_get_webvfs_base_url_rejects_invalid_domino_urls(monkeypatch, domino_url):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.setenv("DOMINO_URL", domino_url)

    with pytest.raises(RuntimeError, match=r"DOMINO_URL must contain a valid HTTP\(S\) host"):
        get_webvfs_base_url()


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
        with pytest.raises(
            RuntimeError,
            match=r"DOMINO_WEBVFS_HOST must contain a valid HTTP\(S\) host",
        ):
            get_webvfs_base_url()
