"""Unit tests for dataset discovery service helpers."""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest
from flask import Flask

from backend.services.data_file_cache import DataFileCache


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _load_datasets_service(monkeypatch):
    chat_agent_stub = types.ModuleType("chat_agent")
    chat_agent_stub.clear_history = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "chat_agent", chat_agent_stub)

    sys.modules.pop("backend.services.datasets", None)
    services = importlib.import_module("backend.services.datasets")
    services.get_file_cache.cache_clear()
    return services


def _install_fake_dataset_client(monkeypatch, dataset_files_by_key):
    captured = {"dataset_keys": []}

    class FakeDataset:
        def __init__(self, files):
            self._files = files

        def list_files(self):
            return [types.SimpleNamespace(name=name) for name in self._files]

    class FakeDatasetClient:
        def __init__(self, token):
            captured["token"] = token

        def get_dataset(self, dataset_key):
            captured["dataset_keys"].append(dataset_key)
            return FakeDataset(dataset_files_by_key[dataset_key])

    domino_data_module = types.ModuleType("domino_data")
    domino_data_datasets_module = types.ModuleType("domino_data.datasets")
    domino_data_datasets_module.DatasetClient = FakeDatasetClient
    domino_data_module.datasets = domino_data_datasets_module
    monkeypatch.setitem(sys.modules, "domino_data", domino_data_module)
    monkeypatch.setitem(sys.modules, "domino_data.datasets", domino_data_datasets_module)
    return captured


def _call_service(app, fn, *args, **kwargs):
    with app.app_context():
        return app.make_response(fn(*args, **kwargs))


def test_list_datasets_via_api_lists_project_datasets_and_netapp_files(monkeypatch):
    services = _load_datasets_service(monkeypatch)
    app = Flask(__name__)

    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(services, "get_domino_api_host", lambda: "https://domino.example")

    api_calls = []

    def fake_requests_get(url, headers=None, timeout=None):
        api_calls.append((url, headers, timeout))
        return _FakeResponse(
            200,
            {
                "datasets": [
                    {"dataset": {"id": "ds-1", "name": "AE", "projectId": "proj-1"}},
                    {"id": "ds-2", "name": "ADSL", "projectId": "proj-1"},
                    {"dataset": {"id": "ds-3", "name": "Other", "projectId": "proj-2"}},
                ]
            },
        )

    monkeypatch.setattr(services.requests, "get", fake_requests_get)

    netapp_calls = []
    monkeypatch.setattr(
        services,
        "discover_netapp_files_for_project",
        lambda project_id, token: netapp_calls.append((project_id, token)) or [
            {
                "display_name": "NetApp Volume/adsl.csv",
                "volume_key": "vol-123",
                "volume_name": "NetApp Volume",
                "volume_id": "nv-1",
            }
        ],
    )

    captured = _install_fake_dataset_client(
        monkeypatch,
        {
            "dataset-AE-ds-1": ["events.csv", "notes.txt"],
            "dataset-ADSL-ds-2": ["subjects.parquet"],
        },
    )

    response = _call_service(app, services.list_datasets_via_api, "proj-1")

    assert response.status_code == 200
    assert response.get_json() == {
        "datasets": [
            "AE/events.csv",
            "ADSL/subjects.parquet",
        ],
        "dataset_info": [
            {"id": "ds-1", "name": "AE"},
            {"id": "ds-2", "name": "ADSL"},
        ],
        "netapp_files": [
            {
                "display_name": "NetApp Volume/adsl.csv",
                "volume_key": "vol-123",
                "volume_name": "NetApp Volume",
                "volume_id": "nv-1",
            }
        ],
        "current_dataset": None,
        "extension_mode": True,
        "project_id": "proj-1",
    }
    assert api_calls == [
        (
            "https://domino.example/api/datasetrw/v2/datasets?projectIdsToInclude=proj-1&limit=100",
            {"Authorization": "Bearer test-token"},
            30,
        )
    ]
    assert netapp_calls == [("proj-1", "test-token")]
    assert captured == {
        "token": "test-token",
        "dataset_keys": [
            "dataset-AE-ds-1",
            "dataset-ADSL-ds-2",
        ],
    }


def test_list_dataset_files_by_id_uses_v1_single_dataset_endpoint(monkeypatch):
    services = _load_datasets_service(monkeypatch)
    app = Flask(__name__)

    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(services, "get_domino_api_host", lambda: "https://domino.example")

    api_calls = []

    def fake_requests_get(url, headers=None, timeout=None):
        api_calls.append((url, headers, timeout))
        return _FakeResponse(200, {"dataset": {"id": "ds-123", "name": "Clinical Study"}})

    monkeypatch.setattr(services.requests, "get", fake_requests_get)

    captured = _install_fake_dataset_client(
        monkeypatch,
        {"dataset-Clinical Study-ds-123": ["adsl.csv", "readme.txt", "adae.parquet"]},
    )

    response = _call_service(app, services.list_dataset_files_by_id, "ds-123")

    assert response.status_code == 200
    assert response.get_json() == {
        "datasets": [
            "Clinical Study/adsl.csv",
            "Clinical Study/adae.parquet",
        ],
        "dataset_info": [{"id": "ds-123", "name": "Clinical Study"}],
        "current_dataset": None,
        "extension_mode": True,
        "dataset_id": "ds-123",
    }
    assert api_calls == [
        (
            "https://domino.example/api/datasetrw/v1/datasets/ds-123",
            {"Authorization": "Bearer test-token"},
            30,
        )
    ]
    assert captured == {
        "token": "test-token",
        "dataset_keys": ["dataset-Clinical Study-ds-123"],
    }


def test_data_file_path_builds_expected_path_and_cleans_up(monkeypatch, tmp_path):
    services = _load_datasets_service(monkeypatch)

    # Force the helper to build its temp tree under pytest's sandbox.
    monkeypatch.setattr(services.tempfile, "gettempdir", lambda: str(tmp_path))

    expected_root = tmp_path / "domino_api_datasets" / "dataset" / "ds-1" / "snap-1"
    expected_path = expected_root / "nested" / "adsl.csv"

    with services.data_file_path("ds-1", "nested/adsl.csv", "dataset", "snap-1") as temp_path:
        # The contextmanager should hand back the fully-qualified file path the
        # caller will write into, including nested subdirectories from file_name.
        assert Path(temp_path) == expected_path
        assert expected_root.is_dir()
        assert expected_path.parent.is_dir()
        expected_path.write_text("new dataset contents", encoding="utf-8")
        assert expected_path.read_text(encoding="utf-8") == "new dataset contents"

    # After the with-block exits, cache-backed cleanup should remove the file
    # and prune the now-empty parent directories for this download path.
    assert not expected_root.exists()


def test_data_file_path_removes_clashing_file_without_touching_other_files(monkeypatch, tmp_path):
    services = _load_datasets_service(monkeypatch)

    monkeypatch.setattr(services.tempfile, "gettempdir", lambda: str(tmp_path))

    file_cache = DataFileCache(temp_root=tmp_path, maxsize=10, ttl=1)

    temp_root = tmp_path / "domino_api_datasets" / "netapp" / "ds-2" / "snap-2"

    stale_file = file_cache.set(source_type="netapp", dataset_id="ds-2", snapshot_id="snap-2", file_name="reports/visit.csv")
    stale_file.write_text("stale contents", encoding="utf-8")

    sibling_file = file_cache.set(source_type="netapp", dataset_id="ds-2", snapshot_id="snap-2", file_name="other.csv")
    sibling_file.write_text("stale sibling", encoding="utf-8")

    with services.data_file_path("ds-2", "reports/visit.csv", "netapp", "snap-2") as temp_path:
        temp_path_obj = Path(temp_path)
        # Reusing the exact same logical download target should clear only that
        # stale file before the caller writes new content into it.
        assert temp_path == stale_file
        assert stale_file.exists()
        assert stale_file.read_text(encoding="utf-8") == ""
        # Unrelated files in the same snapshot directory should be left alone
        assert sibling_file.exists()
        temp_path_obj.write_text("fresh contents", encoding="utf-8")
        assert temp_path_obj.read_text(encoding="utf-8") == "fresh contents"

    assert sibling_file.exists()
    assert not stale_file.exists()


def test_data_file_path_finally_runs_when_with_body_raises(monkeypatch, tmp_path):
    services = _load_datasets_service(monkeypatch)

    monkeypatch.setattr(services.tempfile, "gettempdir", lambda: str(tmp_path))

    expected_root = tmp_path / "domino_api_datasets" / "dataset" / "ds-3" / "snap-3"
    expected_path = expected_root / "adae.parquet"

    with pytest.raises(RuntimeError, match="download failed"):
        with services.data_file_path("ds-3", "adae.parquet", "dataset", "snap-3") as temp_path:
            path_obj = Path(temp_path)
            path_obj.write_text("partial contents", encoding="utf-8")
            assert path_obj == expected_path
            assert expected_path.exists()
            # Simulate a failure after the download path has been created and
            # partially written so we can prove the finally cleanup still runs.
            raise RuntimeError("download failed")

    assert not expected_root.exists()
