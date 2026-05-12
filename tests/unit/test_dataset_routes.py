from flask import Flask

import backend.routes.datasets as dataset_routes


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def _create_test_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(dataset_routes.bp)
    return app


def test_browse_snapshot_files_omits_empty_path_for_root(monkeypatch):
    app = _create_test_app()

    monkeypatch.setattr(dataset_routes, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(dataset_routes, "get_domino_api_host", lambda: "https://domino.example")

    request_calls = []

    def fake_requests_get(url, params=None, headers=None, timeout=None):
        request_calls.append((url, params, headers, timeout))
        return _FakeResponse(
            200,
            {
                "rows": [
                    {"name": {"label": "reports", "isDirectory": True}, "size": {}},
                    {"name": {"label": "adsl.csv", "isDirectory": False}, "size": {"sizeInBytes": 12}},
                ]
            },
        )

    monkeypatch.setattr(dataset_routes.requests, "get", fake_requests_get)

    with app.test_client() as client:
        response = client.get("/snapshot/snap-1/files")

    assert response.status_code == 200
    assert response.get_json() == {
        "entries": [
            {"name": "reports", "isDir": True, "fileName": "reports", "size": "", "path": "reports"},
            {"name": "adsl.csv", "isDir": False, "fileName": "adsl.csv", "size": 12, "path": "adsl.csv"},
        ],
        "snapshotId": "snap-1",
        "currentPath": "",
    }
    assert request_calls == [
        (
            "https://domino.example/v4/datasetrw/files/snap-1",
            None,
            {"Authorization": "Bearer test-token"},
            30,
        )
    ]


def test_browse_snapshot_files_sends_nested_path(monkeypatch):
    app = _create_test_app()

    monkeypatch.setattr(dataset_routes, "get_passthrough_token", lambda: "test-token")
    monkeypatch.setattr(dataset_routes, "get_domino_api_host", lambda: "https://domino.example")

    request_calls = []

    def fake_requests_get(url, params=None, headers=None, timeout=None):
        request_calls.append((url, params, headers, timeout))
        return _FakeResponse(
            200,
            {
                "rows": [
                    {"name": {"label": "adsl.csv", "isDirectory": False}, "size": {"sizeInBytes": 12}},
                ]
            },
        )

    monkeypatch.setattr(dataset_routes.requests, "get", fake_requests_get)

    with app.test_client() as client:
        response = client.get("/snapshot/snap-1/files?path=reports")

    assert response.status_code == 200
    assert response.get_json()["entries"] == [
        {"name": "adsl.csv", "isDir": False, "fileName": "adsl.csv", "size": 12, "path": "reports/adsl.csv"},
    ]
    assert request_calls == [
        (
            "https://domino.example/v4/datasetrw/files/snap-1",
            {"path": "reports"},
            {"Authorization": "Bearer test-token"},
            30,
        )
    ]
