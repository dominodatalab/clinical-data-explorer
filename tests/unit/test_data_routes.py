from flask import Flask, jsonify
import threading
import time

import backend.routes.data as data_routes
import backend.services.dataset_load_request_queue as dataset_load_request_queue_module

from backend.services.dataset_load_request_queue import get_dataset_load_request_queue


def _create_test_app(testing=False):
    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.secret_key = "test-secret"
    app.register_blueprint(data_routes.bp)
    return app


def test_load_dataset_enqueues_filesystem_request(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    captured_requests = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-1")
    monkeypatch.setattr(
        data_routes,
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
    assert captured_requests[0].snapshot_id is None
    assert captured_requests[0].source_type is None
    assert captured_requests[0].volume_key is None
    assert captured_requests[0].snapshot_version is None


def test_load_dataset_enqueues_netapp_request(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    captured_requests = []

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-2")
    monkeypatch.setattr(
        data_routes,
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


def test_load_dataset_raises_when_queue_is_full(monkeypatch):
    full_queue = dataset_load_request_queue_module.DatasetLoadRequestQueue(max_length=0)
    app = _create_test_app(testing=True)

    monkeypatch.setattr(data_routes.dataset_load_request_queue, "get_dataset_load_request_queue", lambda: full_queue)

    with app.test_client() as client:
        response = client.post("/dataset/load", json={"dataset": "datasets/adsl.csv"})

    assert response.status_code == 429
    assert "this server is at capacity." in response.get_data(as_text=True)


def test_load_dataset_returns_413_when_processor_rejects_large_file(monkeypatch):
    app = _create_test_app(testing=True)

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-too-large")
    monkeypatch.setattr(
        data_routes,
        "process_dataset_load_request",
        lambda load_request: (_ for _ in ()).throw(
            data_routes.file_size_limits.DataFileTooLarge("too-big.csv must be less than or equal to 10 bytes to be processable")
        ),
    )

    with app.test_client() as client:
        response = client.post("/dataset/load", json={"dataset": "too-big.csv"})

    assert response.status_code == 413
    assert "too-big.csv must be less than or equal to 10 bytes to be processable" in response.get_data(as_text=True)


def test_load_dataset_serializes_concurrent_requests_through_queue(monkeypatch):
    queue = dataset_load_request_queue_module.DatasetLoadRequestQueue(max_length=10)
    app = _create_test_app()
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()
    state_lock = threading.Lock()
    active_processors = {"count": 0, "max": 0}
    processed = []
    responses = {}

    monkeypatch.setattr(data_routes.dataset_load_request_queue, "get_dataset_load_request_queue", lambda: queue)
    monkeypatch.setattr(data_routes, "get_session_id", lambda: data_routes.request.headers["X-Test-Session-Id"])

    def fake_process_dataset_load_request(load_request):
        with state_lock:
            active_processors["count"] += 1
            active_processors["max"] = max(active_processors["max"], active_processors["count"])
            processed.append((load_request.dataset, load_request.session_id))
            if load_request.dataset == "datasets/one.csv":
                first_started.set()

        try:
            if load_request.dataset == "datasets/one.csv":
                allow_first_to_finish.wait(timeout=1)
            time.sleep(0.01)
            return jsonify({"loaded": True, "dataset": load_request.dataset})
        finally:
            with state_lock:
                active_processors["count"] -= 1

    monkeypatch.setattr(data_routes, "process_dataset_load_request", fake_process_dataset_load_request)

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
    time.sleep(0.02)

    assert queue.qsize() == 2
    assert active_processors["max"] == 1

    allow_first_to_finish.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert responses["datasets/one.csv"].status_code == 200
    assert responses["datasets/two.csv"].status_code == 200
    assert responses["datasets/one.csv"].get_json() == {"loaded": True, "dataset": "datasets/one.csv"}
    assert responses["datasets/two.csv"].get_json() == {"loaded": True, "dataset": "datasets/two.csv"}
    assert processed == [
        ("datasets/one.csv", "sid-1"),
        ("datasets/two.csv", "sid-2"),
    ]
    assert active_processors["max"] == 1
    assert queue.qsize() == 0
