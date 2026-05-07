"""Unit tests for dataset discovery service helpers."""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest
from flask import Flask, jsonify

from backend.services.dataset_load_request_queue import DatasetLoadRequest
from backend.services.download_file_metadata_cache import DownloadFileMetadataCache
from .fixtures import install_fake_dataset_client, install_fake_netapp_client


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

class _FakeStreamingResponse(_FakeResponse):
    def __init__(self, status_code, chunks, payload=None):
        super().__init__(status_code, payload or {})
        self._chunks = chunks

    def iter_content(self, chunk_size=8192):
        del chunk_size
        yield from self._chunks


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
        lambda project_id, token: netapp_calls.append((project_id, token)) or (
            [
                {
                    "display_name": "NetApp Volume/adsl.csv",
                    "volume_key": "vol-123",
                    "volume_name": "NetApp Volume",
                    "volume_id": "nv-1",
                }
            ],
            [
                {
                    "id": "nv-1",
                    "name": "NetApp Volume",
                    "unique_name": "vol-123",
                }
            ],
        ),
    )

    captured = install_fake_dataset_client(
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
        "netapp_volumes": [
            {
                "id": "nv-1",
                "name": "NetApp Volume",
                "unique_name": "vol-123",
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

    captured = install_fake_dataset_client(
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

    file_cache = DownloadFileMetadataCache(temp_root=tmp_path, maxsize=10, ttl=1)

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


@pytest.mark.parametrize(
    ("load_request", "handler_name", "expected_args", "expected_kwargs"),
    [
        (
            DatasetLoadRequest(dataset="datasets/adsl.csv", session_id="sid-local", authorization_header="Bearer token-123"),
            "load_local_dataset_file",
            ("datasets/adsl.csv",),
            {"session_id": "sid-local"},
        ),
        (
            DatasetLoadRequest(dataset="AE/adsl.csv", session_id="sid-proj", authorization_header="Bearer token-123", project_id="proj-1"),
            "load_dataset_via_api",
            ("AE/adsl.csv", "proj-1"),
            {"token": "token-123", "session_id": "sid-proj"},
        ),
        (
            DatasetLoadRequest(dataset="Study/adsl.csv", session_id="sid-ds", authorization_header="Bearer token-123", dataset_id="ds-1"),
            "load_dataset_file_by_id",
            ("Study/adsl.csv", "ds-1"),
            {"token": "token-123", "session_id": "sid-ds"},
        ),
        (
            DatasetLoadRequest(
                dataset="Study/reports/adsl.csv",
                session_id="sid-snap",
                authorization_header="Bearer token-123",
                dataset_id="ds-1",
                snapshot_id="snap-1",
            ),
            "load_dataset_file_from_snapshot",
            ("Study/reports/adsl.csv", "ds-1", "snap-1"),
            {"token": "token-123", "session_id": "sid-snap"},
        ),
        (
            DatasetLoadRequest(
                dataset="Safety Volume/reports/adlb.csv",
                session_id="sid-netapp",
                authorization_header="Bearer token-123",
                source_type="netapp",
                volume_key="vol-1",
                snapshot_version=7,
                snapshot_id="snap-7",
            ),
            "load_netapp_volume_file",
            ("Safety Volume/reports/adlb.csv", "vol-1", 7, "snap-7"),
            {"token": "token-123", "session_id": "sid-netapp"},
        ),
    ],
)
def test_process_dataset_load_request_dispatches_to_correct_loader(monkeypatch, load_request, handler_name, expected_args, expected_kwargs):
    services = _load_datasets_service(monkeypatch)

    captured = []

    def fake_handler(*args, **kwargs):
        captured.append((args, kwargs))
        return "ok"

    monkeypatch.setattr(services, handler_name, fake_handler)

    result = services.process_dataset_load_request(load_request)

    assert result == "ok"
    assert captured == [(expected_args, expected_kwargs)]


def test_validate_dataset_file_size_fetches_metadata_and_enforces_limit(monkeypatch):
    services = _load_datasets_service(monkeypatch)

    monkeypatch.setattr(services, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")

    http_calls = []

    def fake_httpclient_get(url, params=None, headers=None):
        http_calls.append((url, params, headers))
        return {"fileSize": 1234}

    monkeypatch.setattr(services.httpclient, "get", fake_httpclient_get)

    enforce_calls = []
    monkeypatch.setattr(
        services.file_size_limits,
        "enforce",
        lambda file_name, file_size: enforce_calls.append((file_name, file_size)),
    )

    services.validate_dataset_file_size("snap-1", "reports/adsl.csv")

    assert http_calls == [
        (
            "https://domino.example/v4/datasetrw/snapshot/snap-1/file/meta",
            {"path": "reports/adsl.csv"},
            {"Authorization": "Bearer test-token"},
        )
    ]
    assert enforce_calls == [("reports/adsl.csv", 1234)]


def test_load_dataset_via_api_delegates_to_load_dataset_file_by_id(monkeypatch):
    services = _load_datasets_service(monkeypatch)
    app = Flask(__name__)

    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(services, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(services, "get_session_id", lambda: "sid-123")

    request_calls = []

    def fake_requests_get(url, headers=None, timeout=None):
        request_calls.append((url, headers, timeout))
        return _FakeResponse(
            200,
            {
                "datasets": [
                    {"dataset": {"id": "ds-1", "name": "AE", "projectId": "proj-1"}},
                ]
            },
        )

    monkeypatch.setattr(services.requests, "get", fake_requests_get)

    delegated_calls = []

    def fake_load_dataset_file_by_id(dataset_display_name, dataset_id, token=None, session_id=None):
        delegated_calls.append((dataset_display_name, dataset_id, token, session_id))
        return jsonify({"loaded": True, "dataset": dataset_display_name})

    monkeypatch.setattr(services, "load_dataset_file_by_id", fake_load_dataset_file_by_id)

    response = _call_service(app, services.load_dataset_via_api, "AE/reports/visit.csv", "proj-1")

    assert response.status_code == 200
    assert response.get_json() == {"loaded": True, "dataset": "AE/reports/visit.csv"}
    assert request_calls == [
        (
            "https://domino.example/api/datasetrw/v2/datasets?projectId=proj-1&limit=100",
            {"Authorization": "Bearer test-token"},
            30,
        )
    ]
    assert delegated_calls == [("AE/reports/visit.csv", "ds-1", "test-token", "sid-123")]


def test_load_dataset_file_by_id_uses_snapshot_api_and_delegates_to_snapshot_loader(monkeypatch):
    services = _load_datasets_service(monkeypatch)
    app = Flask(__name__)

    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(services, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(services, "get_session_id", lambda: "sid-456")

    snapshot_calls = []

    def fake_httpclient_get(url, params=None, headers=None):
        snapshot_calls.append((url, params, headers))
        return {"snapshots": [{"id": "snap-rw"}]}

    monkeypatch.setattr(services.httpclient, "get", fake_httpclient_get)

    delegated_calls = []

    def fake_load_dataset_file_from_snapshot(dataset_display_name, dataset_id, snapshot_id, token=None, session_id=None):
        delegated_calls.append((dataset_display_name, dataset_id, snapshot_id, token, session_id))
        return jsonify({"loaded": True, "dataset": dataset_display_name})

    monkeypatch.setattr(services, "load_dataset_file_from_snapshot", fake_load_dataset_file_from_snapshot)

    response = _call_service(app, services.load_dataset_file_by_id, "Clinical Study/adsl.csv", "ds-123")

    assert response.status_code == 200
    assert response.get_json() == {"loaded": True, "dataset": "Clinical Study/adsl.csv"}
    assert snapshot_calls == [
        (
            "https://domino.example/api/datasetrw/v1/datasets/ds-123/snapshots",
            {"limit": 1},
            {"Authorization": "Bearer test-token"},
        )
    ]
    assert delegated_calls == [
        ("Clinical Study/adsl.csv", "ds-123", "snap-rw", "test-token", "sid-456")
    ]


def test_load_dataset_file_from_snapshot_uses_data_file_path_without_runtime_errors(monkeypatch, tmp_path):
    services = _load_datasets_service(monkeypatch)
    app = Flask(__name__)

    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(services, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(services.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(services, "get_session_id", lambda: "sid-789")
    validate_calls = []
    monkeypatch.setattr(
        services,
        "validate_dataset_file_size",
        lambda snapshot_id, file_path, token=None, api_host=None: validate_calls.append((snapshot_id, file_path, token, api_host)),
    )

    clear_history_calls = []
    monkeypatch.setattr(services, "clear_history", lambda session_id: clear_history_calls.append(session_id))

    request_calls = []

    def fake_requests_get(url, params=None, headers=None, timeout=None, stream=None):
        request_calls.append((url, params, headers, timeout, stream))
        return _FakeStreamingResponse(200, [b"col1,", b"col2\n1,2\n"])

    monkeypatch.setattr(services.requests, "get", fake_requests_get)

    expected_path = tmp_path / "domino_api_datasets" / "dataset" / "ds-9" / "snap-9" / "adsl.csv"
    mcp_paths = []

    def fake_mcp_post(path, params, session_id=None):
        assert path == "/dataset/load"
        assert session_id == "sid-789"
        temp_path = Path(params["file_snapshot_path"])
        mcp_paths.append(temp_path)
        assert temp_path == expected_path
        assert temp_path.exists()
        assert temp_path.read_bytes() == b"col1,col2\n1,2\n"
        return _FakeResponse(200, {"loaded": True})

    monkeypatch.setattr(services, "mcp_post", fake_mcp_post)

    response = _call_service(
        app,
        services.load_dataset_file_from_snapshot,
        "Clinical Study/reports/adsl.csv",
        "ds-9",
        "snap-9",
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "loaded": True,
        "dataset": "Clinical Study/reports/adsl.csv",
        "sourceType": "dataset",
        "datasetId": "ds-9",
        "snapshotId": "snap-9",
        "governanceFilename": "adsl.csv",
    }
    assert request_calls == [
        (
            "https://domino.example/v4/datasetrw/snapshot/snap-9/file/raw",
            {"path": "reports/adsl.csv", "download": "true"},
            {"Authorization": "Bearer test-token"},
            120,
            True,
        )
    ]
    assert validate_calls == [("snap-9", "reports/adsl.csv", "test-token", "https://domino.example")]
    assert clear_history_calls == ["sid-789"]
    assert mcp_paths == [expected_path]
    assert not expected_path.exists()
    assert not expected_path.parent.exists()


@pytest.mark.parametrize(
    ("snapshot_version", "snapshot_id", "expected_snapshot_dir", "expected_updated_versions"),
    [
        (None, None, "unset_snapshot_id", []),
        (7, "snap-uuid", "7", ["7"]),
    ],
)
def test_load_netapp_volume_file_uses_data_file_path_for_none_and_int_snapshot_versions(
    monkeypatch,
    tmp_path,
    snapshot_version,
    snapshot_id,
    expected_snapshot_dir,
    expected_updated_versions,
):
    services = _load_datasets_service(monkeypatch)
    app = Flask(__name__)

    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(services.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(services, "get_session_id", lambda: "sid-netapp")
    enforce_calls = []
    monkeypatch.setattr(
        services.file_size_limits,
        "enforce",
        lambda file_name, file_size: enforce_calls.append((file_name, file_size)),
    )

    clear_history_calls = []
    monkeypatch.setattr(services, "clear_history", lambda session_id: clear_history_calls.append(session_id))

    netapp_client = install_fake_netapp_client(
        monkeypatch,
        {"vol-123": ["reports/visit.csv"]},
        {"reports/visit.csv": b"VISIT,VALUE\n1,10\n"},
    )

    expected_path = (
        tmp_path
        / "domino_api_datasets"
        / "netapp"
        / "vol-123"
        / expected_snapshot_dir
        / "reports"
        / "visit.csv"
    )
    mcp_paths = []

    def fake_mcp_post(path, params, session_id=None):
        assert path == "/dataset/load"
        assert session_id == "sid-netapp"
        temp_path = Path(params["file_snapshot_path"])
        mcp_paths.append(temp_path)
        assert temp_path == expected_path
        assert temp_path.exists()
        assert temp_path.read_bytes() == b"VISIT,VALUE\n1,10\n"
        return _FakeResponse(200, {"loaded": True})

    monkeypatch.setattr(services, "mcp_post", fake_mcp_post)

    response = _call_service(
        app,
        services.load_netapp_volume_file,
        "Safety Volume/reports/visit.csv",
        "vol-123",
        snapshot_version,
        snapshot_id,
    )

    expected_json = {
        "loaded": True,
        "dataset": "Safety Volume/reports/visit.csv",
        "sourceType": "netapp",
        "volumeId": "nv-1",
        "governanceFilename": "visit.csv",
    }
    if snapshot_version is not None:
        expected_json["snapshotVersion"] = snapshot_version
    if snapshot_id is not None:
        expected_json["snapshotId"] = snapshot_id

    assert response.status_code == 200
    assert response.get_json() == expected_json
    assert netapp_client == {
        "tokens": ["test-token"],
        "get_volume_calls": ["vol-123"],
        "list_files_calls": [] if snapshot_version is not None else ["vol-123"],
        "updated_snapshot_versions": expected_updated_versions,
        "downloaded_files": ["reports/visit.csv"],
    }
    assert clear_history_calls == ["sid-netapp"]
    assert enforce_calls == [("reports/visit.csv", services.file_size_limits.DATA_FILE_SIZE_LIMIT)]
    assert mcp_paths == [expected_path]
    assert not expected_path.exists()
    assert not expected_path.parent.exists()


def test_load_netapp_volume_file_resolves_snapshot_id_to_version_when_version_omitted(
    monkeypatch,
    tmp_path,
):
    """When the netapp deeplink URL provides a snapshot UUID but no version,
    load_netapp_volume_file resolves the version via list_snapshots so the SDK
    can pin reads to that snapshot."""
    services = _load_datasets_service(monkeypatch)
    app = Flask(__name__)

    monkeypatch.setattr(services, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(services.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(services, "get_session_id", lambda: "sid-netapp")
    monkeypatch.setattr(services, "clear_history", lambda session_id: None)

    netapp_client = install_fake_netapp_client(
        monkeypatch,
        {"vol-123": ["diabetes_dataset.csv"]},
        {"diabetes_dataset.csv": b"A,B\n1,2\n"},
    )

    # Set up the snapshot UUID -> version mapping the resolver will look up.
    from domino_data.netapp_volumes import NetAppVolumeClient as FakeClient
    FakeClient.snapshots_by_volume = {
        "vol-123": [
            types.SimpleNamespace(id="snap-uuid-aaa", version=4),
            types.SimpleNamespace(id="snap-uuid-bbb", version=9),
        ]
    }

    monkeypatch.setattr(
        services,
        "mcp_post",
        lambda path, params: _FakeResponse(200, {"loaded": True}),
    )

    response = _call_service(
        app,
        services.load_netapp_volume_file,
        "Safety Volume/diabetes_dataset.csv",
        "vol-123",
        None,         # snapshot_version omitted (URL only carries the UUID)
        "snap-uuid-bbb",
    )

    assert response.status_code == 200
    body = response.get_json()
    # SDK pin happened with the resolved version (9) — captured via volume.update().
    assert netapp_client["updated_snapshot_versions"] == ["9"]
    assert netapp_client.get("list_snapshots_calls") == ["vol-123"]
    assert body["snapshotVersion"] == 9
    assert body["snapshotId"] == "snap-uuid-bbb"
