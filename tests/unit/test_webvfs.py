import pytest

from backend.services.webvfs import get_webvfs_base_url


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("http://127.0.0.1:8763", "http://127.0.0.1:8763/webvfs"),
        (
            "http://data-webvfs-service.custom-compute/",
            "http://data-webvfs-service.custom-compute/webvfs",
        ),
        ("webvfs.internal:8080", "http://webvfs.internal:8080/webvfs"),
        ("  https://webvfs.example  ", "https://webvfs.example/webvfs"),
    ],
)
def test_get_webvfs_base_url_uses_domino_webvfs_host(monkeypatch, host, expected):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)

    assert get_webvfs_base_url() == expected


def test_get_webvfs_base_url_uses_legacy_external_url_fallback(monkeypatch):
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://domino.example/")

    assert get_webvfs_base_url() == "https://domino.example/webvfs"


def test_get_webvfs_base_url_prefers_domino_webvfs_host(monkeypatch):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", "http://127.0.0.1:8763")
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://domino.example")

    assert get_webvfs_base_url() == "http://127.0.0.1:8763/webvfs"


def test_get_webvfs_base_url_ignores_a_blank_domino_webvfs_host(monkeypatch):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", "   ")
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://domino.example")

    assert get_webvfs_base_url() == "https://domino.example/webvfs"


@pytest.mark.parametrize("host", [None, "", "   "])
def test_get_webvfs_base_url_requires_an_explicit_host(monkeypatch, host):
    if host is None:
        monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    else:
        monkeypatch.setenv("DOMINO_WEBVFS_HOST", host)
    monkeypatch.delenv("DOMINO_EXTERNAL_URL", raising=False)

    with pytest.raises(RuntimeError, match="Domino WebVFS host is not configured"):
        get_webvfs_base_url()
