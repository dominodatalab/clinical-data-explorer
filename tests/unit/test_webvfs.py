import pytest
from flask import Flask

import backend.services.webvfs as webvfs
from backend import config
from backend.auth import get_domino_external_url
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


def test_get_domino_external_url_normalizes_override(monkeypatch):
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "  https://external.example/base/  ")

    assert get_domino_external_url() == "https://external.example/base"


def test_get_domino_external_url_ignores_blank_override(monkeypatch):
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "   ")
    monkeypatch.delenv("VSCODE_PROXY_URI", raising=False)

    with Flask(__name__).test_request_context(
        headers={"X-Forwarded-Host": "domino.example", "X-Forwarded-Proto": "https"}
    ):
        assert get_domino_external_url() == "https://domino.example"


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
def test_get_webvfs_base_url_falls_back_to_external_url_for_domino_6_3_1(monkeypatch, host):
    if host is None:
        monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    else:
        monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)
    calls = []

    def get_external_url():
        calls.append(None)
        return "https://domino.example"

    monkeypatch.setattr(webvfs, "get_domino_external_url", get_external_url)

    assert get_webvfs_base_url() == "https://domino.example"
    assert calls == [None]


def test_get_webvfs_base_url_uses_domino_external_url_first(monkeypatch):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "  https://external.example/base/  ")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/123")

    with Flask(__name__).test_request_context(
        headers={
            "X-Forwarded-Host": "apps.domino.example",
            "X-Forwarded-Proto": "https",
        }
    ):
        assert get_webvfs_base_url() == "https://external.example"


def test_get_webvfs_base_url_uses_forwarded_origin_from_external_url_resolver(monkeypatch):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.delenv("DOMINO_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("VSCODE_PROXY_URI", raising=False)

    with Flask(__name__).test_request_context(
        headers={
            "X-Forwarded-Host": "domino.example, proxy.example",
            "X-Forwarded-Proto": "https",
        }
    ):
        assert get_webvfs_base_url() == "https://domino.example"


def test_get_webvfs_base_url_uses_host_and_https_defaults_from_external_url_resolver(
    monkeypatch,
):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.delenv("DOMINO_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("VSCODE_PROXY_URI", raising=False)

    with Flask(__name__).test_request_context(headers={"Host": "domino.example"}):
        assert get_webvfs_base_url() == "https://domino.example"


def test_get_webvfs_base_url_uses_vscode_proxy_from_external_url_resolver(monkeypatch):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.delenv("DOMINO_EXTERNAL_URL", raising=False)
    monkeypatch.setenv("VSCODE_PROXY_URI", "  https://workspace.example/proxy/123  ")

    assert get_webvfs_base_url() == "https://workspace.example"


@pytest.mark.parametrize("external_url", [None, "", "   "])
def test_get_webvfs_base_url_raises_when_no_host_or_external_url_is_available(
    monkeypatch, external_url
):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    if external_url is None:
        monkeypatch.delenv("DOMINO_EXTERNAL_URL", raising=False)
    else:
        monkeypatch.setenv("DOMINO_EXTERNAL_URL", external_url)
    monkeypatch.delenv("VSCODE_PROXY_URI", raising=False)

    with pytest.raises(RuntimeError, match="no external URL is available"):
        get_webvfs_base_url()


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "localhost"},
        {"Host": "domino-platform"},
        {"X-Forwarded-Host": "localhost", "X-Forwarded-Proto": "https"},
        {"X-Forwarded-Host": "domino-platform", "X-Forwarded-Proto": "https"},
    ],
)
def test_get_webvfs_base_url_rejects_internal_origins(monkeypatch, headers):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.delenv("DOMINO_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("VSCODE_PROXY_URI", raising=False)

    with Flask(__name__).test_request_context(headers=headers):
        with pytest.raises(RuntimeError, match="no external URL is available"):
            get_webvfs_base_url()


@pytest.mark.parametrize(
    "external_url",
    [
        "ftp://domino.example",
        "https://user@domino.example",
        "https://",
        "https://domino.example:bad",
    ],
)
def test_get_webvfs_base_url_rejects_invalid_external_urls(monkeypatch, external_url):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.setattr(webvfs, "get_domino_external_url", lambda: external_url)

    with pytest.raises(RuntimeError, match=r"external URL must contain a valid HTTP\(S\) host"):
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
