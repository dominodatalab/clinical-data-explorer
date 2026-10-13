import json

import pytest
from flask import Flask

import backend.chat_tools.admin_auth as admin_auth
import backend.routes.chat_tools_admin as admin_routes
from backend.app import create_app
from backend.chat_tools.catalog import CatalogTool
from backend.chat_tools.policy import PolicyStoreUnavailable

FORBIDDEN = "Chat tool settings are restricted to editors of the extension's project"
DATASET_TOOL = "get_dataset_data_dataset_data_get"


def _tool(name, title=None):
    return CatalogTool(
        name=name,
        title=title or name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        declared_output_example=[{"a": 1}],
    )


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    for name in ("CHAT_TOOL_ADMIN_USERNAMES", "DOMINO_RUN_ID", "DEV_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", str(tmp_path / "chat_tool_policy.json"))
    monkeypatch.setenv("DOMINO_PROJECT_ID", "project-1")
    monkeypatch.setattr(admin_auth, "get_current_user", lambda: {"id": "user-1", "userName": "Alice"})
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: "Owner")
    monkeypatch.setattr(admin_routes, "get_passthrough_token", lambda: "caller-jwt")


@pytest.fixture
def catalog_tools(monkeypatch):
    tools = [_tool("tool_a", "Tool A"), _tool("tool_b"), _tool(DATASET_TOOL)]

    async def list_catalog(authorization_header):
        assert authorization_header == "Bearer caller-jwt"
        return tools

    monkeypatch.setattr(admin_routes.catalog, "list_catalog", list_catalog)
    return tools


@pytest.fixture
def client():
    return create_app().test_client()


def _put(client, body, **kwargs):
    return client.put("/admin/api/chat-tools/policy", json=body, **kwargs)


def _get_policy(client):
    return client.get("/admin/api/chat-tools").get_json()


# ----- who is an admin -----

@pytest.mark.parametrize("role", ["Owner", "Contributor", "contributor", "OWNER"])
def test_owner_and_contributor_are_admins(client, catalog_tools, monkeypatch, role):
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: role)

    assert client.get("/admin/api/chat-tools").status_code == 200


@pytest.mark.parametrize("role", ["Results Consumer", "ResultsConsumer", "Launcher User", "Project Importer", "", None])
def test_other_roles_are_denied(client, monkeypatch, role):
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: role)

    response = client.get("/admin/api/chat-tools")

    assert response.status_code == 403
    assert response.get_json() == {"error": FORBIDDEN}


def test_role_lookup_receives_the_extension_project_id(client, catalog_tools, monkeypatch):
    seen = []
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: seen.append(project_id) or "Owner")

    client.get("/admin/api/chat-tools")

    assert seen == ["project-1"]


def test_failed_role_lookup_fails_closed(client, monkeypatch):
    def boom(project_id):
        raise RuntimeError("domino is down")

    monkeypatch.setattr(admin_auth, "get_caller_project_role", boom)

    assert client.get("/admin/api/chat-tools").status_code == 403


def test_failed_identity_lookup_fails_closed(client, monkeypatch):
    def boom():
        raise RuntimeError("no token")

    monkeypatch.setattr(admin_auth, "get_current_user", boom)

    assert client.get("/admin/api/chat-tools").status_code == 403


def test_missing_project_id_denies_unless_allowlisted(client, catalog_tools, monkeypatch):
    monkeypatch.delenv("DOMINO_PROJECT_ID")
    assert client.get("/admin/api/chat-tools").status_code == 403

    monkeypatch.setenv("CHAT_TOOL_ADMIN_USERNAMES", "someone,ALICE")
    assert client.get("/admin/api/chat-tools").status_code == 200


def test_allowlist_grants_admin_without_a_project_role(client, catalog_tools, monkeypatch):
    monkeypatch.setenv("CHAT_TOOL_ADMIN_USERNAMES", "alice")
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: None)

    assert client.get("/admin/api/chat-tools").status_code == 200


def test_allowlist_does_not_admit_other_users(client, monkeypatch):
    monkeypatch.setenv("CHAT_TOOL_ADMIN_USERNAMES", "bob")
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: None)

    assert client.get("/admin/api/chat-tools").status_code == 403


def test_client_supplied_identity_is_never_trusted(client, monkeypatch):
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: None)

    response = client.get(
        "/admin/api/chat-tools?admin=1&userName=alice",
        headers={"X-Domino-User": "alice", "X-Admin": "true"},
    )

    assert response.status_code == 403


def test_every_admin_route_is_protected(client, monkeypatch):
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: None)
    app = create_app()
    admin_rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/admin/")]
    assert len(admin_rules) >= 6

    with app.test_client() as c:
        for rule in admin_rules:
            path = rule.rule.replace("<name>", "some_tool")
            for method in rule.methods - {"HEAD", "OPTIONS"}:
                response = c.open(path, method=method, json={} if method != "GET" else None)
                assert response.status_code == 403, f"{method} {path}"


def test_non_admin_page_request_gets_a_static_403_page(client, monkeypatch):
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: None)

    response = client.get("/admin/chat-tools")

    assert response.status_code == 403
    assert response.mimetype == "text/html"
    assert FORBIDDEN in response.get_data(as_text=True)


def test_admin_page_is_served_to_admins(client):
    response = client.get("/admin/chat-tools")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert "Chat tool settings" in html
    assert 'src="chat-tools.js"' in html


def test_admin_assets_are_served_next_to_the_page(client):
    assert client.get("/admin/chat-tools.js").status_code == 200
    assert client.get("/admin/chat-tools.css").status_code == 200
    assert client.get("/modules/tool-output.js").status_code == 200


def test_admin_routes_win_over_the_static_catch_all(client, catalog_tools):
    response = client.get("/admin/api/chat-tools")

    assert response.status_code == 200
    assert response.mimetype == "application/json"


# ----- CSRF -----

def test_state_changing_calls_require_json(client, catalog_tools):
    response = client.put(
        "/admin/api/chat-tools/policy",
        data=json.dumps({"expected_revision": 0, "default_mode": "enabled"}),
        content_type="text/plain",
    )

    assert response.status_code == 415


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_fetches_are_rejected(client, catalog_tools, site):
    response = _put(
        client,
        {"expected_revision": 0, "default_mode": "enabled"},
        headers={"Sec-Fetch-Site": site},
    )

    assert response.status_code == 403
    assert _get_policy(client)["policy"]["revision"] == 0


def test_same_origin_fetches_are_accepted(client, catalog_tools):
    response = _put(
        client,
        {"expected_revision": 0, "default_mode": "enabled"},
        headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
    )

    assert response.status_code == 200


def test_foreign_origin_is_rejected(client, catalog_tools):
    response = _put(
        client,
        {"expected_revision": 0, "default_mode": "enabled"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403


def test_origin_may_match_the_forwarded_host(client, catalog_tools):
    response = _put(
        client,
        {"expected_revision": 0, "default_mode": "enabled"},
        headers={"Origin": "https://apps.example.com", "X-Forwarded-Host": "apps.example.com"},
    )

    assert response.status_code == 200


def test_csrf_check_runs_before_the_admin_check(client, monkeypatch):
    monkeypatch.setattr(admin_auth, "get_caller_project_role", lambda project_id: None)

    assert client.put("/admin/api/chat-tools/policy", data="x", content_type="text/plain").status_code == 415


# ----- catalog -----

def test_catalog_lists_tools_with_modes_and_defaults(client, catalog_tools):
    body = _get_policy(client)

    by_name = {tool["name"]: tool for tool in body["tools"]}
    assert set(by_name) == {"tool_a", "tool_b", DATASET_TOOL}
    assert by_name["tool_a"] == {
        "name": "tool_a",
        "title": "Tool A",
        "description": "tool_a description",
        "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
        "declared_output_example": [{"a": 1}],
        "example_args": None,
        "mode": "enabled",
        "note": None,
        "is_default": True,
        "is_new": False,  # nothing is "new" before the first save
    }
    assert by_name[DATASET_TOOL]["mode"] == "disabled"  # built-in default
    assert by_name[DATASET_TOOL]["is_default"] is False
    assert body["policy"]["revision"] == 0
    assert body["policy_error"] is None and body["catalog_error"] is None
    assert body["stale"] == []
    assert body["store"]["durable"] is True
    assert "CDISC" in body["sample"]["label"]


def test_catalog_includes_example_args_for_known_tools(client, catalog_tools, monkeypatch):
    monkeypatch.setattr(
        admin_routes.catalog, "load_preview_examples", lambda names: {"tool_a": {"x": 1}} if "tool_a" in names else {}
    )

    by_name = {tool["name"]: tool for tool in _get_policy(client)["tools"]}

    assert by_name["tool_a"]["example_args"] == {"x": 1}
    assert by_name["tool_b"]["example_args"] is None


def test_catalog_marks_new_and_stale_tools(client, catalog_tools, monkeypatch):
    _put(client, {
        "expected_revision": 0,
        "default_mode": "enabled",
        "tools": {"tool_a": {"mode": "user_only", "note": "review me"}, "removed_tool": {"mode": "disabled"}},
    })
    catalog_tools.append(_tool("brand_new_tool"))

    body = _get_policy(client)

    by_name = {tool["name"]: tool for tool in body["tools"]}
    assert by_name["tool_a"]["mode"] == "user_only"
    assert by_name["tool_a"]["note"] == "review me"
    assert by_name["tool_a"]["is_default"] is False and by_name["tool_a"]["is_new"] is False
    assert by_name["brand_new_tool"]["is_new"] is True
    assert by_name["tool_b"]["is_new"] is True and by_name["tool_b"]["is_default"] is True
    assert body["stale"] == [{"name": "removed_tool", "mode": "disabled", "note": None}]


def test_catalog_error_is_reported_and_stale_is_not_guessed(client, monkeypatch):
    async def down(authorization_header):
        raise ConnectionError("refused")

    monkeypatch.setattr(admin_routes.catalog, "list_catalog", down)
    _put(client, {"expected_revision": 0, "default_mode": "enabled", "tools": {"x": {"mode": "disabled"}}})

    body = _get_policy(client)

    assert body["tools"] == []
    assert "refused" in body["catalog_error"]
    assert body["stale"] == []
    assert body["policy"]["revision"] == 1


def test_invalid_policy_file_is_reported_with_defaults(client, catalog_tools, tmp_path):
    (tmp_path / "chat_tool_policy.json").write_text("{corrupt")

    body = _get_policy(client)

    assert "invalid" in body["policy_error"]
    assert body["policy"]["revision"] is None
    assert len(body["tools"]) == 3


def test_unmounted_store_is_reported(client, catalog_tools, monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", str(tmp_path / "missing" / "chat_tool_policy.json"))

    body = _get_policy(client)

    assert body["store"]["durable"] is False
    assert "missing" in body["store"]["error"]


# ----- saving -----

def test_put_round_trip_updates_the_policy_the_agent_reads(client, catalog_tools):
    response = _put(client, {
        "expected_revision": 0,
        "default_mode": "disabled",
        "tools": {"tool_a": {"mode": "user_only"}, "tool_b": {"mode": "enabled"}},
    })

    assert response.status_code == 200
    policy = response.get_json()["policy"]
    assert policy["revision"] == 1
    assert policy["default_mode"] == "disabled"
    assert policy["updated_by"] == "Alice"
    assert policy["change_log"][0]["by"] == "Alice"

    from backend.chat_tools.policy import ToolMode, get_policy_store
    live = get_policy_store().load()
    assert live.mode_for("tool_a") is ToolMode.USER_ONLY
    assert live.mode_for("unlisted") is ToolMode.DISABLED


def test_put_with_a_stale_revision_conflicts(client, catalog_tools):
    assert _put(client, {"expected_revision": 0, "default_mode": "enabled"}).status_code == 200

    response = _put(client, {"expected_revision": 0, "default_mode": "disabled"})

    assert response.status_code == 409
    assert response.get_json()["error"] == "Someone else saved; reload to see their changes."
    assert _get_policy(client)["policy"]["default_mode"] == "enabled"


@pytest.mark.parametrize("body", [
    {"expected_revision": 0, "default_mode": "sometimes"},
    {"expected_revision": 0, "default_mode": "enabled", "tools": {"t": {"mode": "maybe"}}},
    {"expected_revision": "zero", "default_mode": "enabled"},
    {"default_mode": "enabled"},
    {"expected_revision": 0},
    {"expected_revision": 0, "default_mode": "enabled", "tools": {"t": {"mode": "enabled", "note": "x" * 501}}},
])
def test_put_rejects_invalid_policies(client, catalog_tools, body):
    response = _put(client, body)

    assert response.status_code == 400
    assert _get_policy(client)["policy"]["revision"] == 0


def test_put_reports_an_unwritable_store(client, monkeypatch):
    def refuse(self, policy, expected_revision, actor):
        raise PolicyStoreUnavailable("The settings dataset 'chat-tool-settings' is not mounted")

    monkeypatch.setattr("backend.chat_tools.policy.ChatToolPolicyStore.save", refuse)

    response = _put(client, {"expected_revision": 0, "default_mode": "enabled"})

    assert response.status_code == 503
    assert "not mounted" in response.get_json()["error"]


def test_put_over_a_corrupt_file_is_refused_but_reset_recovers(client, catalog_tools, tmp_path):
    (tmp_path / "chat_tool_policy.json").write_text("{corrupt")

    assert _put(client, {"expected_revision": 0, "default_mode": "enabled"}).status_code == 409

    response = client.post("/admin/api/chat-tools/policy/reset", json={})

    assert response.status_code == 200
    assert _get_policy(client)["policy_error"] is None


def test_reset_restores_built_in_defaults_and_checks_revision(client, catalog_tools):
    _put(client, {"expected_revision": 0, "default_mode": "disabled", "tools": {"tool_a": {"mode": "user_only"}}})

    stale = client.post("/admin/api/chat-tools/policy/reset", json={"expected_revision": 0})
    assert stale.status_code == 409

    response = client.post("/admin/api/chat-tools/policy/reset", json={"expected_revision": 1})

    assert response.status_code == 200
    body = _get_policy(client)
    assert body["policy"]["default_mode"] == "enabled"
    assert {t["name"]: t["mode"] for t in body["tools"]}[DATASET_TOOL] == "disabled"


# ----- preview -----

def test_preview_dataset_loads_the_sample(client, monkeypatch):
    monkeypatch.setattr(admin_routes.preview_sample, "ensure_preview_dataset_loaded", lambda: {
        "columns": ["AGE", "ARM"],
        "numeric_columns": ["AGE"],
        "categorical_columns": ["ARM"],
        "date_columns": [],
        "num_rows": 36,
        "message": "ignored",
    })

    response = client.post("/admin/api/chat-tools/preview/dataset", json={})

    assert response.status_code == 200
    body = response.get_json()
    assert body["columns"] == ["AGE", "ARM"]
    assert body["num_rows"] == 36
    assert "message" not in body
    assert "CDISC" in body["label"]


def test_preview_dataset_failure_is_a_502(client, monkeypatch):
    def boom():
        raise ConnectionError("mcp down")

    monkeypatch.setattr(admin_routes.preview_sample, "ensure_preview_dataset_loaded", boom)

    response = client.post("/admin/api/chat-tools/preview/dataset", json={})

    assert response.status_code == 502
    assert "mcp down" in response.get_json()["error"]


def test_preview_runs_the_tool_in_whatever_mode_it_is_in(client, monkeypatch):
    calls = []
    monkeypatch.setattr(admin_routes.preview_sample, "ensure_preview_dataset_loaded", lambda: calls.append("load"))

    async def call_tool(authorization_header, name, args):
        calls.append((authorization_header, name, args))
        return {"content": [1], "content_text": "[1]", "size_bytes": 3, "approx_tokens": 0, "elapsed_ms": 5}

    monkeypatch.setattr(admin_routes.catalog, "call_tool", call_tool)
    _put(client, {"expected_revision": 0, "default_mode": "enabled", "tools": {"tool_a": {"mode": "disabled"}}})

    response = client.post("/admin/api/chat-tools/tool_a/preview", json={"args": {"x": 1}})

    assert response.status_code == 200
    assert response.get_json()["content"] == [1]
    assert calls == ["load", ("Bearer caller-jwt", "tool_a", {"x": 1})]


def test_preview_reports_tool_errors_in_the_payload(client, monkeypatch):
    monkeypatch.setattr(admin_routes.preview_sample, "ensure_preview_dataset_loaded", lambda: None)

    async def call_tool(authorization_header, name, args):
        return {"content": None, "content_text": None, "size_bytes": 0, "approx_tokens": 0, "elapsed_ms": 1,
                "error": "Error calling t. Status code: 404. Response: {\"detail\":\"Column 'x' not found\"}"}

    monkeypatch.setattr(admin_routes.catalog, "call_tool", call_tool)

    response = client.post("/admin/api/chat-tools/tool_a/preview", json={"args": {}})

    assert response.status_code == 200
    assert "Column 'x' not found" in response.get_json()["error"]


def test_preview_rejects_non_object_args(client):
    response = client.post("/admin/api/chat-tools/tool_a/preview", json={"args": [1]})

    assert response.status_code == 400


def test_preview_defaults_to_empty_args(client, monkeypatch):
    seen = []
    monkeypatch.setattr(admin_routes.preview_sample, "ensure_preview_dataset_loaded", lambda: None)

    async def call_tool(authorization_header, name, args):
        seen.append(args)
        return {"content": {}, "content_text": "{}", "size_bytes": 2, "approx_tokens": 0, "elapsed_ms": 1}

    monkeypatch.setattr(admin_routes.catalog, "call_tool", call_tool)

    client.post("/admin/api/chat-tools/tool_a/preview", json={})

    assert seen == [{}]
