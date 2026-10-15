import pytest
import requests
from werkzeug.exceptions import NotFound

import backend.services.dataset_load_request_file_size_resolver as resolver
from backend.auth import get_domino_webvfs_host
from backend.services.dataset_load_request_queue import DatasetLoadRequest
from backend.services.httpclient import HTTPClientError


@pytest.fixture(autouse=True)
def no_webvfs_host(monkeypatch):
    """Workspaces and Apps set DOMINO_WEBVFS_HOST; tests opt in to it explicitly."""
    monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)


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
    monkeypatch.setattr(resolver, "get_domino_external_url", lambda: "https://domino.example")

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
            "https://domino.example/webvfs/remotefs/v1/volumes/vol-id-123/files/metadata",
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


def test_get_netapp_volume_file_metadata_raises_when_external_url_missing(monkeypatch):
    monkeypatch.setattr(resolver, "get_domino_external_url", lambda: None)

    with pytest.raises(RuntimeError, match="Domino external URL not configured"):
        resolver.get_netapp_volume_file_metadata("vol-id-123", "reports/adsl.csv", token="test-token")


# ----- which host serves /webvfs -----

WEBVFS_URL = "http://127.0.0.1:8763"
EXTERNAL_URL = "https://apps.domino.example"
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


def _configure_hosts(monkeypatch, webvfs=WEBVFS_URL, external=EXTERNAL_URL):
    monkeypatch.setattr(resolver, "get_domino_webvfs_host", lambda: webvfs)
    monkeypatch.setattr(resolver, "get_domino_external_url", lambda: external)


def test_netapp_metadata_prefers_the_in_cluster_webvfs_host(monkeypatch):
    _configure_hosts(monkeypatch)
    urls = _scripted_get(monkeypatch, [{"fileSize": 7}])

    assert resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t") == {"fileSize": 7}
    assert urls == [WEBVFS_URL + METADATA_PATH]


def test_netapp_metadata_falls_back_to_the_external_url_when_the_webvfs_host_404s(monkeypatch):
    _configure_hosts(monkeypatch)
    urls = _scripted_get(monkeypatch, [HTTPClientError(404, "nope"), {"fileSize": 7}])

    assert resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t") == {"fileSize": 7}
    assert urls == [WEBVFS_URL + METADATA_PATH, EXTERNAL_URL + METADATA_PATH]


def test_netapp_metadata_falls_back_when_the_webvfs_host_is_unreachable(monkeypatch):
    _configure_hosts(monkeypatch)
    urls = _scripted_get(monkeypatch, [requests.exceptions.ConnectionError("refused"), {"fileSize": 7}])

    assert resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t") == {"fileSize": 7}
    assert len(urls) == 2


def test_netapp_metadata_without_a_webvfs_host_uses_the_external_url(monkeypatch):
    _configure_hosts(monkeypatch, webvfs=None)
    urls = _scripted_get(monkeypatch, [{"fileSize": 7}])

    resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert urls == [EXTERNAL_URL + METADATA_PATH]


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_netapp_metadata_does_not_retry_errors_that_a_different_host_wont_fix(monkeypatch, status):
    """A missing file, a denied token or a server error is the answer, not a wrong host."""
    _configure_hosts(monkeypatch)
    urls = _scripted_get(monkeypatch, [HTTPClientError(status, "no"), {"fileSize": 7}])

    with pytest.raises(HTTPClientError) as raised:
        resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert raised.value.status_code == status
    assert len(urls) == 1


def test_netapp_metadata_raises_the_last_error_when_every_host_404s(monkeypatch):
    _configure_hosts(monkeypatch)
    urls = _scripted_get(monkeypatch, [HTTPClientError(404, "first"), HTTPClientError(404, "last")])

    with pytest.raises(HTTPClientError, match="last"):
        resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert len(urls) == 2


def test_netapp_metadata_does_not_call_the_same_host_twice(monkeypatch):
    _configure_hosts(monkeypatch, webvfs=EXTERNAL_URL, external=EXTERNAL_URL)
    urls = _scripted_get(monkeypatch, [HTTPClientError(404, "nope")])

    with pytest.raises(HTTPClientError):
        resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t")

    assert urls == [EXTERNAL_URL + METADATA_PATH]


def test_netapp_metadata_explicit_external_url_is_still_honored(monkeypatch):
    _configure_hosts(monkeypatch, webvfs=None, external=None)
    urls = _scripted_get(monkeypatch, [{"fileSize": 7}])

    resolver.get_netapp_volume_file_metadata("vol-id-123", "a.csv", token="t", external_url="https://given.example")

    assert urls == ["https://given.example" + METADATA_PATH]


@pytest.mark.parametrize("raw, expected", [
    ("http://127.0.0.1:8763", "http://127.0.0.1:8763"),
    ("http://127.0.0.1:8763/", "http://127.0.0.1:8763"),
    ("127.0.0.1:8763", "http://127.0.0.1:8763"),
    ("  https://webvfs.internal  ", "https://webvfs.internal"),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_webvfs_host_is_normalized(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("DOMINO_WEBVFS_HOST", raising=False)
    else:
        monkeypatch.setenv("DOMINO_WEBVFS_HOST", raw)

    assert get_domino_webvfs_host() == expected
