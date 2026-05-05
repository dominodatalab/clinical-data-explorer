from flask import Flask, jsonify

import backend.routes.data as data_routes
import backend.services.dataset_load_request_queue as dataset_load_request_queue_module

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


def test_load_dataset_enqueues_filesystem_request(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-1")

    clear_history_calls = []
    monkeypatch.setattr(data_routes, "clear_history", lambda session_id: clear_history_calls.append(session_id))

    def fake_mcp_post(path, params):
        assert path == "/dataset/load"
        assert params == {"file_snapshot_path": "datasets/adsl.csv"}
        return _FakeMcpResponse(200, {"loaded": True, "dataset": "datasets/adsl.csv"})

    monkeypatch.setattr(data_routes, "mcp_post", fake_mcp_post)

    with app.test_client() as client:
        response = client.post(
            "/dataset/load",
            json={"dataset": "datasets/adsl.csv"},
            headers={"Authorization": "Bearer token-1"},
        )

    assert response.status_code == 200
    assert response.get_json() == {"loaded": True, "dataset": "datasets/adsl.csv"}
    assert clear_history_calls == ["sid-1"]

    entries = queue.peek_all()
    assert len(entries) == 1
    assert entries[0].dataset == "datasets/adsl.csv"
    assert entries[0].session_id == "sid-1"
    assert entries[0].authorization_header == "Bearer token-1"
    assert entries[0].project_id is None
    assert entries[0].dataset_id is None
    assert entries[0].snapshot_id is None
    assert entries[0].source_type is None
    assert entries[0].volume_key is None
    assert entries[0].snapshot_version is None


def test_load_dataset_enqueues_netapp_request(monkeypatch):
    queue = get_dataset_load_request_queue()
    queue.clear()
    app = _create_test_app()

    monkeypatch.setattr(data_routes, "get_session_id", lambda: "sid-2")

    def fake_load_netapp_volume_file(dataset_name, volume_key, snapshot_version, snapshot_id):
        assert dataset_name == "Safety Volume/reports/adlb.csv"
        assert volume_key == "vol-123"
        assert snapshot_version == 7
        assert snapshot_id == "snap-7"
        return jsonify({"loaded": True, "dataset": dataset_name})

    monkeypatch.setattr(data_routes, "load_netapp_volume_file", fake_load_netapp_volume_file)

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

    entries = queue.peek_all()
    assert len(entries) == 1
    assert entries[0].dataset == "Safety Volume/reports/adlb.csv"
    assert entries[0].session_id == "sid-2"
    assert entries[0].authorization_header == "Bearer token-2"
    assert entries[0].source_type == "netapp"
    assert entries[0].volume_key == "vol-123"
    assert entries[0].snapshot_version == 7
    assert entries[0].snapshot_id == "snap-7"


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

    monkeypatch.setattr(data_routes, "get_dataset_load_request_queue", lambda: full_queue)

    with app.test_client() as client:
        response = client.post("/dataset/load", json={"dataset": "datasets/adsl.csv"})

    assert response.status_code == 429
    assert "this server is at capacity." in response.get_data(as_text=True)
