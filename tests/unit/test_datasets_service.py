"""Unit tests for dataset discovery service helpers."""

import importlib
import json
import sys
import types

from flask import Flask


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
    return importlib.import_module("backend.services.datasets")


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
