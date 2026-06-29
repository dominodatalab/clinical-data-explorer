import pytest
from werkzeug.exceptions import NotFound

import backend.services.dataset_load_request_file_size_resolver as resolver
from backend.services.dataset_load_request_queue import DatasetLoadRequest


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
        resolver._get_dataset_snapshot_file_size("reports/adsl.csv", "snap-1")


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


def test_resolve_dataset_file_size_uses_explicit_file_path(monkeypatch):
    calls = []
    monkeypatch.setattr(resolver, "get_passthrough_token_from_authorization_header", lambda header: "test-token")
    monkeypatch.setattr(resolver, "_get_default_dataset_snapshot_id", lambda dataset_id, token=None: "snap-1")

    def fake_get_dataset_snapshot_file_metadata(snapshot_id, file_path, token=None, api_host=None):
        calls.append((snapshot_id, file_path, token))
        return {"fileSize": 12345}

    monkeypatch.setattr(resolver, "get_dataset_snapshot_file_metadata", fake_get_dataset_snapshot_file_metadata)

    file_size = resolver.resolve_dataset_load_request_file_size(
        DatasetLoadRequest(
            dataset="Clinical Dataset",
            session_id="sid-1",
            authorization_header="Bearer passthrough",
            dataset_id="ds-1",
            file_path="nested/adsl.csv",
        )
    )

    assert file_size == 12345
    assert calls == [("snap-1", "nested/adsl.csv", "test-token")]


def test_get_netapp_volume_file_size_raises_for_missing_volume_id():
    with pytest.raises(RuntimeError, match="Missing NetApp volume ID for reports/adsl.csv"):
        resolver._get_netapp_volume_file_size("reports/adsl.csv", None, token="test-token")


def test_get_netapp_volume_file_size_raises_not_found_for_missing_file_size(monkeypatch):
    monkeypatch.setattr(resolver, "get_netapp_volume_file_metadata", lambda *args, **kwargs: {})

    with pytest.raises(NotFound, match="Missing fileSize in metadata for reports/adsl.csv"):
        resolver._get_netapp_volume_file_size("reports/adsl.csv", "vol-id-123", token="test-token")


def test_get_netapp_volume_file_metadata_raises_when_external_url_missing(monkeypatch):
    monkeypatch.setattr(resolver, "get_domino_external_url", lambda: None)

    with pytest.raises(RuntimeError, match="Domino external URL not configured"):
        resolver.get_netapp_volume_file_metadata("vol-id-123", "reports/adsl.csv", token="test-token")
