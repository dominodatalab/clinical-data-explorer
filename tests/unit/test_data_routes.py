from flask import Flask, jsonify
import pytest
import threading
import time

import backend.routes.data as data_routes
import backend.services.dataframe_reload as dataframe_reload
import backend.services.dataset_reload_context as dataset_reload_context
import backend.services.dataset_load_request_queue as dataset_load_request_queue_module
import backend.services.datasets as datasets_service

from backend.services.dataset_load_request_queue import get_dataset_load_request_queue


class _FakeMcpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _create_test_app(testing=False):
    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.secret_key = "test-secret"
    app.register_blueprint(data_routes.bp)
    return app


@pytest.fixture(autouse=True)
def stub_queue_mcp_dataframe_hooks(monkeypatch):
    reload_context_cache = {}

    def fake_reload_context_get(path, session_id=None, **kwargs):
        assert path == "/dataframe/current-session"
        return _FakeMcpResponse(200, {"dataset": None, "reload_context": reload_context_cache.get(session_id)})

    monkeypatch.setattr(dataset_reload_context, "mcp_get", fake_reload_context_get)
    monkeypatch.setattr(dataset_reload_context, "_test_reload_context_cache", reload_context_cache, raising=False)

    queue = get_dataset_load_request_queue()
    monkeypatch.setattr(
        dataset_load_request_queue_module.DatasetLoadRequestQueue,
        "_get_current_session_dataframe_size_bytes",
        lambda self, session_id: 0,
    )
    monkeypatch.setattr(
        dataset_load_request_queue_module.DatasetLoadRequestQueue,
        "_evict_current_session_dataframe",
        lambda self, session_id: None,
    )
    monkeypatch.setattr(queue, "_get_current_session_dataframe_size_bytes", lambda session_id: 0)
    monkeypatch.setattr(queue, "_evict_current_session_dataframe", lambda session_id: None)


def test_load_dataset_enqueues_filesystem_request(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    captured_requests = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-1")
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)
    monkeypatch.setattr(
        dataframe_reload,
        "process_dataset_load_request",
        lambda load_request: captured_requests.append(load_request) or jsonify({"loaded": True, "dataset": load_request.dataset}),
    )

    with app.test_client() as client:
        response = client.post(
            "/dataset/load",
            json={"dataset": "datasets/adsl.csv"},
            headers={"Authorization": "Bearer token-1"},
        )

    assert response.status_code == 200
    assert response.get_json() == {"loaded": True, "dataset": "datasets/adsl.csv"}
    assert queue.peek_all() == []

    assert len(captured_requests) == 1
    assert captured_requests[0].dataset == "datasets/adsl.csv"
    assert captured_requests[0].session_id == "sid-1"
    assert captured_requests[0].authorization_header == "Bearer token-1"
    assert captured_requests[0].project_id is None
    assert captured_requests[0].dataset_id is None
    assert captured_requests[0].file_path is None
    assert captured_requests[0].snapshot_id is None
    assert captured_requests[0].source_type is None
    assert captured_requests[0].volume_key is None
    assert captured_requests[0].snapshot_version is None


def test_load_dataset_enqueues_file_path_request(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    captured_requests = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-file-path")
    monkeypatch.setattr(
        dataframe_reload,
        "resolve_dataset_load_target",
        lambda load_request: datasets_service.DatasetLoadTarget(
            file_snapshot_path="/tmp/dataset/ds-1/snap-1/nested/adsl.csv",
        ),
    )
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)
    monkeypatch.setattr(
        dataframe_reload,
        "process_dataset_load_request",
        lambda load_request: captured_requests.append(load_request) or jsonify({"loaded": True, "dataset": load_request.dataset}),
    )

    with app.test_client() as client:
        response = client.post(
            "/dataset/load",
            json={
                "dataset": "Clinical Dataset",
                "datasetId": "ds-1",
                "filePath": "nested/adsl.csv",
            },
            headers={"Authorization": "Bearer token-1"},
        )

    assert response.status_code == 200
    assert len(captured_requests) == 1
    assert captured_requests[0].dataset == "Clinical Dataset"
    assert captured_requests[0].dataset_id == "ds-1"
    assert captured_requests[0].file_path == "nested/adsl.csv"


def test_load_dataset_reuses_matching_session_dataframe_without_queueing(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()
    calls = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-reuse")
    monkeypatch.setattr(
        dataframe_reload,
        "mcp_get",
        lambda path, session_id=None, **kwargs: calls.append(("get", path, session_id))
        or _FakeMcpResponse(200, {"dataset": "datasets/adsl.csv"}),
    )
    monkeypatch.setattr(
        datasets_service,
        "mcp_post",
        lambda path, params=None, session_id=None, **kwargs: calls.append(("post", path, params, session_id))
        or _FakeMcpResponse(200, {"dataset": "datasets/adsl.csv", "columns": ["USUBJID"], "num_rows": 1}),
    )
    monkeypatch.setattr(
        dataframe_reload.dataset_load_request_queue,
        "resolve_dataset_load_request_file_size",
        lambda load_request: (_ for _ in ()).throw(AssertionError("should not resolve file size")),
    )
    monkeypatch.setattr(
        dataframe_reload,
        "process_dataset_load_request",
        lambda load_request: (_ for _ in ()).throw(AssertionError("should not process load request")),
    )

    with app.test_client() as client:
        response = client.post(
            "/dataset/load",
            json={"dataset": "datasets/adsl.csv"},
            headers={"Authorization": "Bearer token-1"},
        )

    assert response.status_code == 200
    assert response.get_json() == {"dataset": "datasets/adsl.csv", "columns": ["USUBJID"], "num_rows": 1}
    assert calls == [
        ("get", "/dataframe/current-session", "sid-reuse"),
        ("post", "/dataset/load", {"file_snapshot_path": "datasets/adsl.csv"}, "sid-reuse"),
    ]
    assert queue.peek_all() == []


def test_load_dataset_enqueues_netapp_request(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    captured_requests = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-2")
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)
    monkeypatch.setattr(
        dataframe_reload,
        "process_dataset_load_request",
        lambda load_request: captured_requests.append(load_request) or jsonify({"loaded": True, "dataset": load_request.dataset}),
    )

    with app.test_client() as client:
        response = client.post(
            "/dataset/load",
            json={
                "dataset": "Safety Volume/reports/adlb.csv",
                "sourceType": "netapp",
                "volumeKey": "vol-123",
                "volumeId": "vol-id-123",
                "snapshotVersion": 7,
                "snapshotId": "snap-7",
            },
            headers={"Authorization": "Bearer token-2"},
        )

    assert response.status_code == 200
    assert response.get_json() == {"loaded": True, "dataset": "Safety Volume/reports/adlb.csv"}
    assert queue.peek_all() == []

    assert len(captured_requests) == 1
    assert captured_requests[0].dataset == "Safety Volume/reports/adlb.csv"
    assert captured_requests[0].session_id == "sid-2"
    assert captured_requests[0].authorization_header == "Bearer token-2"
    assert captured_requests[0].source_type == "netapp"
    assert captured_requests[0].volume_key == "vol-123"
    assert captured_requests[0].volume_id == "vol-id-123"
    assert captured_requests[0].snapshot_version == 7
    assert captured_requests[0].snapshot_id == "snap-7"


def test_load_dataset_does_not_enqueue_invalid_request():
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    with app.test_client() as client:
        response = client.post("/dataset/load", json={})

    assert response.status_code == 400
    assert response.get_json() == {"error": "No dataset name provided"}
    assert queue.peek_all() == []


def test_load_dataset_evicts_stale_dataframes_before_resolving_file_size(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()
    order = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-evict-first")

    def fake_mcp_post(path, **kwargs):
        order.append(("evict", path))
        return _FakeMcpResponse(200, {"evicted_sessions": 1, "evicted_dataframes": 1})

    def fake_resolve(load_request):
        order.append(("resolve", load_request.dataset))
        return 1

    def fake_process(load_request):
        order.append(("process", load_request.dataset))
        return jsonify({"loaded": True, "dataset": load_request.dataset})

    monkeypatch.setattr(dataframe_reload, "mcp_post", fake_mcp_post)
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", fake_resolve)
    monkeypatch.setattr(dataframe_reload, "process_dataset_load_request", fake_process)

    with app.test_client() as client:
        response = client.post("/dataset/load", json={"dataset": "datasets/adsl.csv"})

    assert response.status_code == 200
    assert order == [
        ("evict", "/dataframes/evict-stale"),
        ("resolve", "datasets/adsl.csv"),
        ("process", "datasets/adsl.csv"),
    ]
    assert queue.peek_all() == []


def test_load_dataset_raises_when_queue_is_full(monkeypatch):
    full_queue = dataset_load_request_queue_module.DatasetLoadRequestQueue(max_length=0)
    app = _create_test_app(testing=True)

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-queue-full")
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "get_dataset_load_request_queue", lambda: full_queue)
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)

    with app.test_client() as client:
        response = client.post("/dataset/load", json={"dataset": "datasets/adsl.csv"})

    assert response.status_code == 429
    assert "this server is at capacity." in response.get_data(as_text=True)


def test_load_dataset_returns_413_when_processor_rejects_large_file(monkeypatch):
    app = _create_test_app(testing=True)

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-too-large")
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)
    monkeypatch.setattr(
        dataframe_reload,
        "process_dataset_load_request",
        lambda load_request: (_ for _ in ()).throw(
            dataframe_reload.file_size_limits.DataFileTooLarge("too-big.csv must be less than or equal to 10 bytes to be processable")
        ),
    )

    with app.test_client() as client:
        response = client.post("/dataset/load", json={"dataset": "too-big.csv"})

    assert response.status_code == 413
    assert "too-big.csv must be less than or equal to 10 bytes to be processable" in response.get_data(as_text=True)


def test_load_dataset_surfaces_mcp_dataset_load_error(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()
    mcp_calls = []
    mcp_error = (
        "Dataset 'datasets/too-big.csv' is too large to load right now. "
        "Try a smaller file or ask your administrator to increase the amount of memory available."
    )

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-mcp-failure")
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)

    def fake_mcp_post(path, params, session_id=None):
        mcp_calls.append((path, params, session_id))
        return _FakeMcpResponse(413, {"detail": mcp_error})

    monkeypatch.setattr(datasets_service, "mcp_post", fake_mcp_post)

    with app.test_client() as client:
        response = client.post("/dataset/load", json={"dataset": "datasets/too-big.csv"})

    assert response.status_code == 413
    assert response.get_json() == {"error": mcp_error}
    assert mcp_calls == [
        (
            "/dataset/load",
            {"file_snapshot_path": "datasets/too-big.csv"},
            "sid-mcp-failure",
        )
    ]
    assert queue.peek_all() == []


def test_table_data_reloads_expired_dataframe_from_saved_context(monkeypatch):
    app = _create_test_app()
    loaded_requests = []
    table_calls = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-reload")
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)
    monkeypatch.setattr(
        data_routes,
        "mcp_get",
        lambda path, session_id=None, **kwargs: _FakeMcpResponse(200, {"dataset": None}),
    )

    def fake_mcp_post(path, json=None, **kwargs):
        if path == "/dataframes/evict-stale":
            return _FakeMcpResponse(200, {"evicted_dataframes": 1})
        if path == "/table/data":
            table_calls.append(json)
            if len(table_calls) == 1:
                return _FakeMcpResponse(400, {"detail": {"error": "No dataset loaded."}})
            return _FakeMcpResponse(200, {"rows": [{"USUBJID": "01"}], "total": 1})
        raise AssertionError(f"unexpected MCP POST path: {path}")

    monkeypatch.setattr(
        dataframe_reload,
        "process_dataset_load_request",
        lambda load_request: loaded_requests.append(load_request) or jsonify({"loaded": True, "dataset": load_request.dataset}),
    )
    monkeypatch.setattr(data_routes, "mcp_post", fake_mcp_post)

    with app.test_client() as client:
        load_response = client.post(
            "/dataset/load",
            json={
                "dataset": "Clinical Dataset",
                "datasetId": "ds-1",
                "snapshotId": "snap-1",
                "filePath": "nested/adsl.csv",
            },
        )
        dataset_reload_context._test_reload_context_cache["sid-reload"] = {
            "dataset": "Clinical Dataset",
            "datasetId": "ds-1",
            "snapshotId": "snap-1",
            "filePath": "nested/adsl.csv",
        }
        response = client.post("/table/data", json={"page": 1, "page_size": 100})

    assert load_response.status_code == 200
    assert response.status_code == 200
    assert response.get_json() == {"rows": [{"USUBJID": "01"}], "total": 1}
    assert len(loaded_requests) == 2
    assert [(request.dataset, request.dataset_id, request.snapshot_id, request.file_path) for request in loaded_requests] == [
        ("Clinical Dataset", "ds-1", "snap-1", "nested/adsl.csv"),
        ("Clinical Dataset", "ds-1", "snap-1", "nested/adsl.csv"),
    ]
    assert len(table_calls) == 2


def test_table_data_reports_actionable_error_when_expired_data_has_no_reload_context(monkeypatch):
    app = _create_test_app()

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-missing-context")
    monkeypatch.setattr(
        data_routes,
        "mcp_post",
        lambda path, json=None, **kwargs: _FakeMcpResponse(400, {"detail": {"error": "No dataset loaded."}}),
    )

    with app.test_client() as client:
        response = client.post("/table/data", json={"page": 1, "page_size": 100})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Your data expired and couldn't be reloaded. Please select the file again.",
    }


def test_table_data_reports_no_space_when_expired_data_reload_is_too_large(monkeypatch):
    app = _create_test_app(testing=True)
    load_attempts = {"count": 0}

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-reload-too-large")
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)
    monkeypatch.setattr(
        data_routes,
        "mcp_get",
        lambda path, session_id=None, **kwargs: _FakeMcpResponse(200, {"dataset": None}),
    )

    def fake_mcp_post(path, json=None, **kwargs):
        if path == "/dataframes/evict-stale":
            return _FakeMcpResponse(200, {"evicted_dataframes": 1})
        if path == "/table/data":
            return _FakeMcpResponse(400, {"detail": {"error": "No dataset loaded."}})
        raise AssertionError(f"unexpected MCP POST path: {path}")

    def fake_process_dataset_load_request(load_request):
        load_attempts["count"] += 1
        if load_attempts["count"] == 1:
            return jsonify({"loaded": True, "dataset": load_request.dataset})
        raise dataframe_reload.file_size_limits.DataFileTooLarge(
            "Clinical Dataset/nested/adsl.csv must be less than or equal to 10 bytes to be processable"
        )

    monkeypatch.setattr(data_routes, "mcp_post", fake_mcp_post)
    monkeypatch.setattr(dataframe_reload, "process_dataset_load_request", fake_process_dataset_load_request)

    with app.test_client() as client:
        load_response = client.post(
            "/dataset/load",
            json={
                "dataset": "Clinical Dataset",
                "datasetId": "ds-1",
                "snapshotId": "snap-1",
                "filePath": "nested/adsl.csv",
            },
        )
        dataset_reload_context._test_reload_context_cache["sid-reload-too-large"] = {
            "dataset": "Clinical Dataset",
            "datasetId": "ds-1",
            "snapshotId": "snap-1",
            "filePath": "nested/adsl.csv",
        }
        response = client.post("/table/data", json={"page": 1, "page_size": 100})

    assert load_response.status_code == 200
    assert response.status_code == 413
    assert response.get_json() == {
        "error": "Your data expired and we couldn't reload it because there's not enough space",
    }


def test_load_dataset_processes_concurrent_requests_when_memory_allows(monkeypatch):
    queue = dataset_load_request_queue_module.DatasetLoadRequestQueue(max_length=10)
    app = _create_test_app()
    first_started = threading.Event()
    second_started = threading.Event()
    allow_first_to_finish = threading.Event()
    state_lock = threading.Lock()
    active_processors = {"count": 0, "max": 0}
    processed = []
    responses = {}

    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "get_dataset_load_request_queue", lambda: queue)
    monkeypatch.setattr(data_routes, "get_session_id", lambda: data_routes.request.headers["X-Test-Session-Id"])
    monkeypatch.setattr(dataframe_reload.dataset_load_request_queue, "resolve_dataset_load_request_file_size", lambda load_request: 1)

    def fake_process_dataset_load_request(load_request):
        with state_lock:
            active_processors["count"] += 1
            active_processors["max"] = max(active_processors["max"], active_processors["count"])
            processed.append((load_request.dataset, load_request.session_id))
            if load_request.dataset == "datasets/one.csv":
                first_started.set()
            if load_request.dataset == "datasets/two.csv":
                second_started.set()

        try:
            allow_first_to_finish.wait(timeout=1)
            time.sleep(0.01)
            return jsonify({"loaded": True, "dataset": load_request.dataset})
        finally:
            with state_lock:
                active_processors["count"] -= 1

    monkeypatch.setattr(dataframe_reload, "process_dataset_load_request", fake_process_dataset_load_request)

    def post_dataset(name, session_id):
        with app.test_client() as client:
            responses[name] = client.post(
                "/dataset/load",
                json={"dataset": name},
                headers={
                    "Authorization": f"Bearer {session_id}",
                    "X-Test-Session-Id": session_id,
                },
            )

    first_thread = threading.Thread(target=post_dataset, args=("datasets/one.csv", "sid-1"))
    second_thread = threading.Thread(target=post_dataset, args=("datasets/two.csv", "sid-2"))

    first_thread.start()
    assert first_started.wait(timeout=1)

    second_thread.start()
    assert second_started.wait(timeout=1)

    assert queue.qsize() == 2
    assert active_processors["max"] == 2

    allow_first_to_finish.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert responses["datasets/one.csv"].status_code == 200
    assert responses["datasets/two.csv"].status_code == 200
    assert responses["datasets/one.csv"].get_json() == {"loaded": True, "dataset": "datasets/one.csv"}
    assert responses["datasets/two.csv"].get_json() == {"loaded": True, "dataset": "datasets/two.csv"}
    assert set(processed) == {
        ("datasets/one.csv", "sid-1"),
        ("datasets/two.csv", "sid-2"),
    }
    assert active_processors["max"] == 2
    assert queue.qsize() == 0
