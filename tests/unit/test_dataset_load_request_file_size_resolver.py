import pytest
from werkzeug.exceptions import NotFound

import backend.services.dataset_load_request_file_size_resolver as resolver


def test_resolve_project_dataset_id_raises_not_found(monkeypatch):
    monkeypatch.setattr(resolver, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(resolver, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(resolver.httpclient, "get", lambda *args, **kwargs: {"datasets": []})

    with pytest.raises(NotFound, match='Dataset "AE" not found in project'):
        resolver._resolve_project_dataset_id("AE/adsl.csv", "proj-1")


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
