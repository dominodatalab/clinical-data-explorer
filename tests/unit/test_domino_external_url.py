import pytest
from flask import Flask

from backend.auth import get_domino_external_url
from backend.services.governance import get_governance_api_url


@pytest.mark.parametrize("domino_url", ["https://domino.example", "http://localhost:8080/"])
def test_external_url_returns_domino_url_without_request_context(monkeypatch, domino_url):
    monkeypatch.setenv("DOMINO_URL", domino_url)

    assert get_domino_external_url() == domino_url


@pytest.mark.parametrize("domino_url", [None, "", "https://domino.example"])
def test_external_url_ignores_other_origins(monkeypatch, domino_url):
    if domino_url is None:
        monkeypatch.delenv("DOMINO_URL", raising=False)
    else:
        monkeypatch.setenv("DOMINO_URL", domino_url)
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
        assert get_domino_external_url() == domino_url


@pytest.mark.parametrize("domino_url", ["https://domino.example", "https://domino.example/"])
def test_governance_url_uses_deployment_origin(monkeypatch, domino_url):
    monkeypatch.setenv("DOMINO_URL", domino_url)

    with Flask(__name__).test_request_context(base_url="https://apps.domino.example"):
        assert get_governance_api_url() == "https://domino.example/api/governance/v1"


@pytest.mark.parametrize("domino_url", [None, "", "   "])
def test_governance_url_requires_domino_url(monkeypatch, domino_url, caplog):
    if domino_url is None:
        monkeypatch.delenv("DOMINO_URL", raising=False)
    else:
        monkeypatch.setenv("DOMINO_URL", domino_url)

    assert get_governance_api_url() is None
    assert "DOMINO_URL is not configured" in caplog.text
