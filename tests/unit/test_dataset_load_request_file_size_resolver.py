import pytest
import requests
from flask import Flask
from werkzeug.exceptions import NotFound

import backend.services.dataset_load_request_file_size_resolver as resolver
from backend.services.dataset_load_request_queue import DatasetLoadRequest
from backend.services.httpclient import HTTPClientError


@pytest.fixture(autouse=True)
def no_webvfs_configuration(monkeypatch):
    for name in ("DOMINO_WEBVFS_HOST", "DOMINO_URL", "DOMINO_EXTERNAL_URL", "VSCODE_PROXY_URI"):
        monkeypatch.delenv(name, raising=False)


def test_resolve_project_dataset_id_raises_not_found(monkeypatch):
    monkeypatch.setattr(resolver, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(resolver, "get_passthrough_token", lambda: "test-token")

    def fake_get(url, **kwargs):
        if url.endswith("/api/projects/v1/projects/proj-1/shared-datasets"):
            return {"dataset": {"projectId": "proj-1", "sharedDatasetIds": []}}
        return {"datasets": []}

    monkeypatch.setattr(resolver.httpclient, "get", fake_get)

    with pytest.raises(NotFound, match='Dataset "AE" not found in project'):
        resolver._resolve_project_dataset_id("AE/adsl.csv", "proj-1")


def test_resolve_project_dataset_id_finds_shared_mount(monkeypatch):
    monkeypatch.setattr(resolver, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(resolver, "get_passthrough_token", lambda: "test-token")
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/datasetrw/v2/datasets"):
            return {"datasets": []}
        if url.endswith("/api/projects/v1/projects/proj-1/shared-datasets"):
            return {"dataset": {"projectId": "proj-1", "sharedDatasetIds": ["ds-shared"]}}
        if url.endswith("/api/datasetrw/v1/datasets/ds-shared"):
            return {"dataset": {"id": "ds-shared", "name": "quick-start"}}
        return {}

    monkeypatch.setattr(resolver.httpclient, "get", fake_get)

    assert resolver._resolve_project_dataset_id("quick-start/adsl.csv", "proj-1") == "ds-shared"
    assert calls == [
        (
            "https://domino.example/api/datasetrw/v2/datasets",
            {
                "params": {"projectIdsToInclude": "proj-1", "limit": 100},
                "headers": {"Authorization": "Bearer test-token"},
            },
        ),
        (
            "https://domino.example/api/projects/v1/projects/proj-1/shared-datasets",
            {"headers": {"Authorization": "Bearer test-token"}},
        ),
        (
            "https://domino.example/api/datasetrw/v1/datasets/ds-shared",
            {"headers": {"Authorization": "Bearer test-token"}},
        ),
    ]


def test_get_default_dataset_snapshot_id_raises_not_found(monkeypatch):
    monkeypatch.setattr(resolver, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(resolver, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(resolver.httpclient, "get", lambda *args, **kwargs: {"snapshots": []})

    with pytest.raises(NotFound, match="No snapshots found for dataset ds-1"):
        resolver._get_default_dataset_snapshot_id("ds-1")


def test_get_dataset_snapshot_file_size_raises_not_found_for_missing_file_size(monkeypatch):
    monkeypatch.setattr(resolver, "get_dataset_snapshot_file_metadata", lambda *args, **kwargs: {})

    with pytest.raises(NotFound, match="Missing fileSize in metadata for reports/adsl.csv"):
        resolver._get_dataset_snapshot_file_size("Study/reports/adsl.csv", "snap-1")


def test_resolve_netapp_volume_file_size_from_webvfs_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(resolver, "get_passthrough_token_from_authorization_header", lambda header: "test-token")
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", "https://webvfs.example/")

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return {
            "exceedsSizeLimit": False,
            "previewBlocked": False,
            "fileSize": 88859,
            "lastModified": 1780005039000,
            "mimeType": "application/vnd.apache.parquet",
            "name": "clinical1.parquet",
            "previewUri": "/webvfs/remotefs/v1/volumes/vol-id-123/files/preview/clinical1.parquet",
            "uri": "/webvfs/remotefs/v1/volumes/vol-id-123/files/raw/clinical1.parquet",
        }

    monkeypatch.setattr(resolver.httpclient, "get", fake_get)

    file_size = resolver.resolve_dataset_load_request_file_size(
        DatasetLoadRequest(
            dataset="Safety Volume/clinical1.parquet",
            session_id="sid-1",
            authorization_header="Bearer passthrough",
            source_type="netapp",
            volume_key="vol-123",
            volume_id="vol-id-123",
        )
    )

    assert file_size == 88859
    assert calls == [
        (
            "https://webvfs.example/webvfs/remotefs/v1/volumes/vol-id-123/files/metadata",
            {
                "params": {"path": "clinical1.parquet"},
                "headers": {
                    "accept": "application/json",
                    "Authorization": "Bearer test-token",
                },
            },
        )
    ]


def test_get_netapp_volume_file_size_raises_for_missing_volume_id():
    with pytest.raises(RuntimeError, match="Missing NetApp volume ID for Safety Volume/reports/adsl.csv"):
        resolver._get_netapp_volume_file_size("Safety Volume/reports/adsl.csv", None, token="test-token")


def test_get_netapp_volume_file_size_raises_not_found_for_missing_file_size(monkeypatch):
    monkeypatch.setattr(resolver, "get_netapp_volume_file_metadata", lambda *args, **kwargs: {})

    with pytest.raises(NotFound, match="Missing fileSize in metadata for reports/adsl.csv"):
        resolver._get_netapp_volume_file_size("Safety Volume/reports/adsl.csv", "vol-id-123", token="test-token")


WEBVFS_URL = "http://127.0.0.1:8763"
DOMINO_URL = "https://domino.example"
METADATA_PATH = "/webvfs/remotefs/v1/volumes/vol-id-123/files/metadata"


def _scripted_get(monkeypatch, outcomes):
    """Replace httpclient.get: each call pops the next outcome (a result, or an exception to raise)."""
    urls = []

    def fake_get(url, **kwargs):
        urls.append(url)
        outcome = outcomes[len(urls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(resolver.httpclient, "get", fake_get)
    return urls


def test_netapp_metadata_uses_only_the_configured_webvfs_host(monkeypatch):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", WEBVFS_URL)
    monkeypatch.setenv("DOMINO_URL", DOMINO_URL)
    monkeypatch.setenv("DOMINO_API_HOST", "https://api.domino.example")
    urls = _scripted_get(monkeypatch, [{"fileSize": 7}])

    with Flask(__name__).test_request_context(
        headers={
            "Host": "apps.domino.example",
            "X-Forwarded-Host": "apps.domino.example",
            "X-Forwarded-Proto": "https",
        }
    ):
        metadata = resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert metadata == {"fileSize": 7}
    assert urls == [WEBVFS_URL + METADATA_PATH]


def test_netapp_metadata_uses_domino_url_and_ignores_app_headers(monkeypatch):
    monkeypatch.setenv("DOMINO_URL", DOMINO_URL)
    monkeypatch.setenv("DOMINO_EXTERNAL_URL", "https://external.example")
    monkeypatch.setenv("VSCODE_PROXY_URI", "https://workspace.example/proxy/123")
    urls = _scripted_get(monkeypatch, [{"fileSize": 7}])

    with Flask(__name__).test_request_context(
        headers={
            "Host": "apps.domino.example",
            "X-Forwarded-Host": "apps.domino.example, proxy.domino.example",
            "X-Forwarded-Proto": "https",
        }
    ):
        metadata = resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert metadata == {"fileSize": 7}
    assert urls == [DOMINO_URL + METADATA_PATH]


@pytest.mark.parametrize("name", ["DOMINO_URL", "DOMINO_EXTERNAL_URL", "VSCODE_PROXY_URI"])
def test_netapp_metadata_uses_external_url_without_request_context(monkeypatch, name):
    monkeypatch.setenv(name, f"  {DOMINO_URL}/  ")
    urls = _scripted_get(monkeypatch, [{"fileSize": 7}])

    metadata = resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert metadata == {"fileSize": 7}
    assert urls == [DOMINO_URL + METADATA_PATH]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_netapp_metadata_raises_before_request_when_no_external_url_is_available(monkeypatch, value):
    if value is not None:
        for name in ("DOMINO_URL", "DOMINO_EXTERNAL_URL", "VSCODE_PROXY_URI"):
            monkeypatch.setenv(name, value)
    calls = []
    monkeypatch.setattr(resolver.httpclient, "get", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(RuntimeError, match="no external URL is available"):
        resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert calls == []


@pytest.mark.parametrize(
    "error",
    [
        HTTPClientError(404, "not found"),
        requests.exceptions.ConnectionError("refused"),
        requests.exceptions.Timeout("timed out"),
    ],
)
def test_netapp_metadata_propagates_webvfs_errors_without_fallback(monkeypatch, error):
    monkeypatch.setenv("DOMINO_WEBVFS_HOST", WEBVFS_URL)
    monkeypatch.setenv("DOMINO_URL", DOMINO_URL)
    urls = _scripted_get(monkeypatch, [error])

    with pytest.raises(type(error)) as raised:
        resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert raised.value is error
    assert urls == [WEBVFS_URL + METADATA_PATH]
