import pytest
from flask import Flask

import backend.routes.launch_context as launch_context_route
import backend.services.launch_context as launch_context


@pytest.fixture(autouse=True)
def no_external_url_configuration(monkeypatch):
    for name in ("DOMINO_URL", "DOMINO_EXTERNAL_URL", "VSCODE_PROXY_URI"):
        monkeypatch.delenv(name, raising=False)


def test_resolve_launch_context_returns_noop_when_run_id_is_absent(monkeypatch):
    monkeypatch.setattr(launch_context.config, "get_domino_run_id", lambda: None)

    assert launch_context.resolve_launch_context() == {
        "redirectUrl": None,
        "available": False,
        "reason": "DOMINO_RUN_ID is not set",
    }


@pytest.mark.parametrize("name", ["DOMINO_URL", "DOMINO_EXTERNAL_URL"])
def test_resolve_launch_context_builds_project_sidebar_redirect(monkeypatch, name):
    calls = []
    monkeypatch.setattr(launch_context.config, "get_domino_run_id", lambda: "instance-1")
    monkeypatch.setattr(launch_context, "get_passthrough_token", lambda: "token-1")
    monkeypatch.setattr(launch_context, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setenv(name, "  https://external.example/  ")

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/apps/v1/apps"):
            return {
                "items": [
                    {
                        "id": "app-1",
                        "project": {
                            "id": "proj-1",
                            "ownerUsername": "integration-test",
                            "name": "CDE",
                        },
                        "currentVersion": {
                            "id": "version-1",
                            "currentInstance": {"id": "instance-1"},
                        },
                    }
                ],
                "metadata": {"offset": 0, "limit": 50, "totalCount": 1},
            }
        if url.endswith("/api/extensions/beta/extensions-ui"):
            return {
                "data": [
                    {
                        "id": "ext-other",
                        "appId": "app-other",
                        "uiMountPointTypeConfigs": {
                            "projectSidebar": {"title": "Other"},
                        },
                    },
                    {
                        "id": "ext-1",
                        "appId": "app-1",
                        "uiMountPointTypeConfigs": {
                            "projectSidebar": {"title": "CDE"},
                        },
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(launch_context.httpclient, "get", fake_get)

    with Flask(__name__).test_request_context(
        headers={"Host": "apps.domino.example", "X-Forwarded-Host": "apps.domino.example"}
    ):
        result = launch_context.resolve_launch_context()

    assert result == {
        "redirectUrl": (
            "https://external.example/u/integration-test/CDE/extension"
            "?mountPointType=projectSidebar&extensionId=ext-1&projectId=proj-1"
        ),
        "available": True,
        "projectId": "proj-1",
        "extensionId": "ext-1",
        "ownerUsername": "integration-test",
        "projectName": "CDE",
    }
    assert [url for url, _ in calls] == [
        "https://domino.example/api/apps/v1/apps",
        "https://domino.example/api/extensions/beta/extensions-ui",
    ]
    assert calls[0][1]["headers"]["Authorization"] == "Bearer token-1"
    assert calls[0][1]["params"] == {
        "limit": 50,
        "offset": 0,
        "sortField": "lastViewed",
        "sortOrder": "desc",
    }
    assert calls[1][1]["params"] == {
        "mount_point_type": "projectSidebar",
        "project_id": "proj-1",
    }
    assert not any("/api/projects/" in url for url, _ in calls)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_resolve_launch_context_requires_external_url(monkeypatch, value):
    monkeypatch.setattr(launch_context.config, "get_domino_run_id", lambda: "instance-1")
    monkeypatch.setattr(launch_context, "get_passthrough_token", lambda: "token-1")
    monkeypatch.setattr(launch_context, "get_domino_api_host", lambda: "https://domino.example")
    if value is not None:
        for name in ("DOMINO_URL", "DOMINO_EXTERNAL_URL", "VSCODE_PROXY_URI"):
            monkeypatch.setenv(name, value)

    def unexpected_get(*args, **kwargs):
        pytest.fail("App discovery must not run without an external URL")

    monkeypatch.setattr(launch_context.httpclient, "get", unexpected_get)

    assert launch_context.resolve_launch_context() == {
        "redirectUrl": None,
        "available": False,
        "reason": "Domino API host or external URL is not configured",
    }


def test_resolve_launch_context_scans_app_pages_until_instance_is_found(monkeypatch):
    calls = []
    monkeypatch.setattr(launch_context.config, "get_domino_run_id", lambda: "instance-1")
    monkeypatch.setattr(launch_context, "get_passthrough_token", lambda: "token-1")
    monkeypatch.setattr(launch_context, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(launch_context, "get_domino_external_url", lambda: "https://external.example")

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/apps/v1/apps"):
            offset = kwargs["params"]["offset"]
            if offset == 0:
                return {
                    "items": [
                        {
                            "id": "app-1",
                            "project": {
                                "id": "proj-1",
                                "ownerUsername": "integration-test",
                                "name": "CDE",
                            },
                            "currentVersion": {
                                "id": "version-1",
                                "currentInstance": {"id": "other-instance"},
                            },
                        }
                    ],
                    "metadata": {"offset": 0, "limit": 50, "totalCount": 51},
                }
            return {
                "items": [
                    {
                        "id": "app-2",
                        "project": {
                            "id": "proj-2",
                            "ownerUsername": "integration-test",
                            "name": "CDE",
                        },
                        "currentVersion": {
                            "id": "version-2",
                            "currentInstance": {"id": "instance-1"},
                        },
                    }
                ],
                "metadata": {"offset": 50, "limit": 50, "totalCount": 51},
            }
        if url.endswith("/api/extensions/beta/extensions-ui"):
            return {
                "data": [
                    {
                        "id": "ext-1",
                        "appId": "app-2",
                        "uiMountPointTypeConfigs": {
                            "projectSidebar": {"title": "CDE"},
                        },
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(launch_context.httpclient, "get", fake_get)

    assert launch_context.resolve_launch_context()["projectId"] == "proj-2"
    assert [kwargs["params"]["offset"] for url, kwargs in calls if url.endswith("/api/apps/v1/apps")] == [0, 50]


def test_resolve_launch_context_returns_reason_when_extension_missing(monkeypatch):
    monkeypatch.setattr(launch_context.config, "get_domino_run_id", lambda: "instance-1")
    monkeypatch.setattr(launch_context, "get_passthrough_token", lambda: "token-1")
    monkeypatch.setattr(launch_context, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(launch_context, "get_domino_external_url", lambda: "https://external.example")

    def fake_get(url, **kwargs):
        if url.endswith("/api/apps/v1/apps"):
            return {
                "items": [
                    {
                        "id": "app-1",
                        "project": {
                            "id": "proj-1",
                            "ownerUsername": "integration-test",
                            "name": "CDE",
                        },
                        "currentVersion": {
                            "id": "version-1",
                            "currentInstance": {"id": "instance-1"},
                        },
                    }
                ],
                "metadata": {"offset": 0, "limit": 50, "totalCount": 1},
            }
        if url.endswith("/api/extensions/beta/extensions-ui"):
            return {"data": []}
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(launch_context.httpclient, "get", fake_get)

    assert launch_context.resolve_launch_context() == {
        "redirectUrl": None,
        "available": False,
        "projectId": "proj-1",
        "reason": "No enabled project sidebar extension was found for this app's project",
    }


def test_resolve_launch_context_contextualizes_app_list_failures(monkeypatch):
    monkeypatch.setattr(launch_context.config, "get_domino_run_id", lambda: "instance-1")
    monkeypatch.setattr(launch_context, "get_passthrough_token", lambda: "token-1")
    monkeypatch.setattr(launch_context, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(launch_context, "get_domino_external_url", lambda: "https://external.example")

    def fake_get(url, **kwargs):
        if url.endswith("/api/apps/v1/apps"):
            raise ValueError("network failed")
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(launch_context.httpclient, "get", fake_get)

    assert launch_context.resolve_launch_context()["reason"] == (
        "Failed to list Domino apps at offset 0: network failed"
    )


def test_resolve_launch_context_contextualizes_extension_fetch_failures(monkeypatch):
    monkeypatch.setattr(launch_context.config, "get_domino_run_id", lambda: "instance-1")
    monkeypatch.setattr(launch_context, "get_passthrough_token", lambda: "token-1")
    monkeypatch.setattr(launch_context, "get_domino_api_host", lambda: "https://domino.example")
    monkeypatch.setattr(launch_context, "get_domino_external_url", lambda: "https://external.example")

    def fake_get(url, **kwargs):
        if url.endswith("/api/apps/v1/apps"):
            return {
                "items": [
                    {
                        "id": "app-1",
                        "project": {
                            "id": "proj-1",
                            "ownerUsername": "integration-test",
                            "name": "CDE",
                        },
                        "currentVersion": {
                            "id": "version-1",
                            "currentInstance": {"id": "instance-1"},
                        },
                    }
                ],
                "metadata": {"offset": 0, "limit": 50, "totalCount": 1},
            }
        if url.endswith("/api/extensions/beta/extensions-ui"):
            raise ValueError("extensions unavailable")
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(launch_context.httpclient, "get", fake_get)

    assert launch_context.resolve_launch_context()["reason"] == (
        "Failed to fetch project sidebar extensions for project proj-1: extensions unavailable"
    )


def test_launch_context_route_returns_service_payload(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(launch_context_route.bp)
    monkeypatch.setattr(
        launch_context_route,
        "resolve_launch_context",
        lambda: {"redirectUrl": "https://external.example/u/user/project/extension"},
    )

    with app.test_client() as client:
        response = client.get("/launch-context")

    assert response.status_code == 200
    assert response.get_json() == {"redirectUrl": "https://external.example/u/user/project/extension"}
