from flask import Flask

from backend.auth import get_domino_external_url


def test_domino_external_url_overrides_forwarded_app_host(monkeypatch):
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://domino.example/")

    with Flask(__name__).test_request_context(
        headers={
            "Host": "apps.domino.example",
            "X-Forwarded-Host": "apps.domino.example",
            "X-Forwarded-Proto": "https",
        }
    ):
        assert get_domino_external_url() == "https://domino.example"
