import pytest
from flask import Flask

from backend.auth import get_domino_external_url
from backend.services.governance import get_governance_api_url


@pytest.fixture(autouse=True)
def no_external_url_configuration(monkeypatch):
    for name in ("DOMINO_URL", "DOMINO_EXTERNAL_URL", "VSCODE_PROXY_URI", "DOMINO_API_HOST"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", ["DOMINO_URL", "DOMINO_EXTERNAL_URL"])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://domino.example", "https://domino.example"),
        ("  https://domino.example/base///  ", "https://domino.example/base"),
        ("http://localhost:8080/", "http://localhost:8080"),
    ],
)
def test_external_url_normalizes_configured_url_without_request_context(
    monkeypatch, name, value, expected
):
    monkeypatch.setenv(name, value)

    assert get_domino_external_url() == expected


def test_external_url_prefers_domino_url_over_other_origins(monkeypatch):
    monkeypatch.setenv("DOMINO_URL", "  https://domino.example/  ")
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://override.example")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/8888")
    monkeypatch.setenv("DOMINO_API_HOST", "http://nucleus-frontend.domino-platform:80")

    with Flask(__name__).test_request_context(
        headers={
            "Host": "app-123.apps.domino.example",
            "X-Forwarded-Host": "apps.domino.example, proxy.example",
            "X-Forwarded-Proto": "http",
        }
    ):
        assert get_domino_external_url() == "https://domino.example"


@pytest.mark.parametrize("domino_url", [None, "", "   "])
def test_external_url_falls_back_to_override_before_headers(monkeypatch, domino_url):
    if domino_url is not None:
        monkeypatch.setenv("DOMINO_URL", domino_url)
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "  https://override.example/  ")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/8888")

    with Flask(__name__).test_request_context(base_url="https://apps.domino.example"):
        assert get_domino_external_url() == "https://override.example"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (
            {
                "Host": "app.example",
                "X-Forwarded-Host": "  domino.example/, proxy.example",
                "X-Forwarded-Proto": "http",
            },
            "http://domino.example",
        ),
        ({"Host": "domino.example"}, "https://domino.example"),
        ({"X-Forwarded-Host": "domino.example"}, "https://domino.example"),
    ],
)
def test_external_url_uses_request_headers_before_workspace_proxy(monkeypatch, headers, expected):
    monkeypatch.setenv("DOMINO_URL", "   ")
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "   ")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/8888")

    with Flask(__name__).test_request_context(headers=headers):
        assert get_domino_external_url() == expected


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "localhost:5000"},
        {"X-Forwarded-Host": "nucleus-frontend.domino-platform:80"},
    ],
)
def test_external_url_skips_internal_request_hosts(monkeypatch, headers):
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/8888")

    with Flask(__name__).test_request_context(headers=headers):
        assert get_domino_external_url() == "https://workspace.example"


def test_external_url_uses_workspace_proxy_origin_without_request_context(monkeypatch):
    monkeypatch.setenv("VSCODE_PROXY_URI", "  https://workspace.example:8443/proxy/8888/  ")

    assert get_domino_external_url() == "https://workspace.example:8443"


def test_external_url_can_select_app_ingress_without_configured_public_url():
    with Flask(__name__).test_request_context(base_url="https://apps.domino.example"):
        assert get_domino_external_url() == "https://apps.domino.example"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_external_url_returns_none_without_any_candidate(monkeypatch, value):
    if value is not None:
        for name in ("DOMINO_URL", "DOMINO_EXTERNAL_URL", "VSCODE_PROXY_URI"):
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("DOMINO_API_HOST", "http://nucleus-frontend.domino-platform:80")

    assert get_domino_external_url() is None


@pytest.mark.parametrize("proxy_uri", ["/proxy/8888", "https://[invalid"])
def test_external_url_ignores_invalid_workspace_proxy(monkeypatch, proxy_uri):
    monkeypatch.setenv("VSCODE_PROXY_URI", proxy_uri)

    assert get_domino_external_url() is None


@pytest.mark.parametrize("name", ["DOMINO_URL", "DOMINO_EXTERNAL_URL"])
def test_governance_url_uses_normalized_external_url(monkeypatch, name):
    monkeypatch.setenv(name, "  https://domino.example/  ")

    with Flask(__name__).test_request_context(base_url="https://apps.domino.example"):
        assert get_governance_api_url() == "https://domino.example/api/governance/v1"


def test_governance_url_requires_external_url():
    assert get_governance_api_url() is None
