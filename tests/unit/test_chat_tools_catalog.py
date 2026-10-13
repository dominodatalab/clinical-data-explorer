"""Catalog parsing, previews and the project-role lookup, with the network mocked."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from flask import Flask
from pydantic_ai import ModelRetry

import backend.chat_tools.admin_auth as admin_auth
import backend.chat_tools.catalog as catalog
import backend.chat_tools.preview_sample as preview_sample


# ----- catalog parsing -----

FEATURE_STATS = (
    "Get Feature Stats\n\nGet detailed statistics for specified features or all features\n\n"
    "### Responses:\n\n**200**: Successful Response (Success Response)\nContent-Type: application/json\n\n"
    "**Example Response:**\n```json\n[\n  {\n    \"feature\": \"Feature\",\n    \"missing_values\": 1\n  }\n]\n```"
)


def test_split_description_extracts_the_declared_output_example():
    body, example = catalog.split_description(FEATURE_STATS)

    assert body == "Get Feature Stats\n\nGet detailed statistics for specified features or all features"
    assert example == [{"feature": "Feature", "missing_values": 1}]


def test_split_description_without_an_example_block():
    description = "Get Dataset Head\n\nGet the first n rows\n\n### Responses:\n\n**200**: Successful Response\nContent-Type: application/json"

    body, example = catalog.split_description(description)

    assert body == "Get Dataset Head\n\nGet the first n rows"
    assert example is None


def test_split_description_keeps_unparseable_examples_as_text_and_handles_none():
    body, example = catalog.split_description("T\n\n### Responses:\n\n**Example Response:**\n```json\nnot json\n```")
    assert example == "not json"
    assert catalog.split_description(None) == ("", None)
    assert catalog.split_description("Plain description") == ("Plain description", None)


def test_to_catalog_tool_uses_first_line_as_title_and_falls_back_to_the_name():
    tool = catalog.to_catalog_tool(SimpleNamespace(
        name="get_feature_stats_feature_stats_get",
        description=FEATURE_STATS,
        inputSchema={"type": "object", "properties": {"features": {"type": "array"}}},
    ))

    assert tool.title == "Get Feature Stats"
    assert tool.input_schema["properties"]["features"] == {"type": "array"}
    bare = catalog.to_catalog_tool(SimpleNamespace(name="t", description=None, inputSchema=None))
    assert bare.title == "t"
    assert bare.input_schema == {"type": "object", "properties": {}}


# ----- MCP calls -----

class FakeServer:
    instances = []

    def __init__(self, url, headers, read_timeout=None):
        self.url, self.headers = url, headers
        self.calls = []
        FakeServer.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def list_tools(self):
        return [SimpleNamespace(name="a", description="A\n\nbody", inputSchema={"type": "object"})]

    async def direct_call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "fails":
            raise ModelRetry("Error calling fails. Status code: 404. Response: {\"detail\":\"Column 'x' not found\"}")
        if name == "text":
            return "plain text"
        return [{"AGE": 71.0, "ARM": "Placebo"}] * 4


@pytest.fixture
def fake_server(monkeypatch):
    FakeServer.instances = []
    monkeypatch.setattr(catalog, "MCPServerSSE", FakeServer)
    return FakeServer


def test_list_catalog_connects_with_the_callers_token_in_the_preview_namespace(fake_server):
    tools = asyncio.run(catalog.list_catalog("Bearer jwt"))

    assert [t.name for t in tools] == ["a"]
    assert fake_server.instances[0].headers == {"Authorization": "Bearer jwt", "X-Chat-Tool-Preview": "1"}
    assert fake_server.instances[0].url.endswith("/mcp")


def test_call_tool_reports_what_the_model_would_receive(fake_server):
    result = asyncio.run(catalog.call_tool("Bearer jwt", "head", {"rows": 4}))

    expected_text = json.dumps([{"AGE": 71.0, "ARM": "Placebo"}] * 4, ensure_ascii=False)
    assert result["content"] == [{"AGE": 71.0, "ARM": "Placebo"}] * 4
    assert result["content_text"] == expected_text
    assert result["size_bytes"] == len(expected_text.encode())
    assert result["approx_tokens"] == len(expected_text) // 4
    assert result["elapsed_ms"] >= 0
    assert "error" not in result
    assert fake_server.instances[0].calls == [("head", {"rows": 4})]


def test_call_tool_handles_text_results(fake_server):
    result = asyncio.run(catalog.call_tool("Bearer jwt", "text", {}))

    assert result["content_text"] == "plain text"
    assert result["size_bytes"] == len("plain text")


def test_call_tool_returns_failures_as_errors_with_full_text(fake_server):
    result = asyncio.run(catalog.call_tool("Bearer jwt", "fails", {}))

    assert result["content"] is None
    assert "Column 'x' not found" in result["error"]
    assert result["size_bytes"] == 0


# ----- preview sample -----

def test_sample_csv_exists_and_is_small_and_covers_each_parameter_type():
    import pandas as pd

    df = pd.read_csv(preview_sample.SAMPLE_PATH)

    assert preview_sample.SAMPLE_PATH.is_file()
    assert preview_sample.SAMPLE_PATH.stat().st_size < 50_000
    assert len(df) == 36
    assert df["ARM"].value_counts().tolist() == [12, 12, 12]
    assert {"AGE", "BMIBL"} <= set(df.select_dtypes("number").columns)
    assert not any(pd.api.types.is_numeric_dtype(df[c]) for c in ("ARM", "SEX", "RACE"))
    assert pd.to_datetime(df["TRTSDT"]).notna().all()
    assert "datasets" not in preview_sample.SAMPLE_PATH.parts  # never in local dataset discovery


def test_ensure_preview_dataset_loaded_posts_to_the_preview_session(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            seen["checked"] = True

        def json(self):
            return {"columns": ["AGE"]}

    def fake_post(path, **kwargs):
        seen.update(path=path, **kwargs)
        return Response()

    monkeypatch.setattr(preview_sample, "mcp_post", fake_post)

    assert preview_sample.ensure_preview_dataset_loaded() == {"columns": ["AGE"]}
    assert seen["path"] == "/dataset/load"
    assert seen["params"] == {"file_snapshot_path": str(preview_sample.SAMPLE_PATH)}
    assert seen["headers"] == {"X-Chat-Tool-Preview": "1"}
    assert seen["checked"] is True


# ----- project role lookup -----

@pytest.fixture
def role_lookup(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr(admin_auth, "get_domino_api_host", lambda: "http://domino")
    monkeypatch.setattr(admin_auth, "get_passthrough_token", lambda: "caller-jwt")
    monkeypatch.setattr(admin_auth, "get_current_user", lambda: {"id": "user-1", "userName": "alice"})
    calls = []

    def respond(status, payload=None):
        class Response:
            status_code = status

            def raise_for_status(self):
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")

            def json(self):
                return payload

        def fake_get(url, headers, timeout):
            calls.append((url, headers))
            return Response()

        monkeypatch.setattr(admin_auth.requests, "get", fake_get)

    def lookup():
        with app.test_request_context():
            return admin_auth.get_caller_project_role("project-1")

    return respond, lookup, calls


def test_role_lookup_uses_the_callers_token(role_lookup):
    respond, lookup, calls = role_lookup
    respond(200, {"ownerId": "user-1", "collaborators": []})

    assert lookup() == "Owner"
    assert calls == [("http://domino/v4/projects/project-1", {"Authorization": "Bearer caller-jwt", "Accept": "application/json"})]


def test_role_lookup_finds_a_collaborator_role(role_lookup):
    respond, lookup, _ = role_lookup
    respond(200, {"ownerId": "someone-else", "collaborators": [
        {"collaboratorId": "user-2", "projectRole": "Owner"},
        {"collaboratorId": "user-1", "projectRole": "Contributor"},
    ]})

    assert lookup() == "Contributor"


def test_role_lookup_is_none_for_non_members_and_hidden_projects(role_lookup):
    respond, lookup, _ = role_lookup
    respond(200, {"ownerId": "someone-else", "collaborators": [{"collaboratorId": "user-2", "projectRole": "Owner"}]})
    assert lookup() is None
    respond(200, {"ownerId": "someone-else"})
    assert lookup() is None
    for status in (401, 403, 404):
        respond(status)
        assert lookup() is None


def test_role_lookup_raises_on_server_errors_so_the_caller_fails_closed(role_lookup):
    respond, lookup, _ = role_lookup
    respond(500)

    with pytest.raises(RuntimeError):
        lookup()


def test_role_lookup_needs_an_api_host_and_token(role_lookup, monkeypatch):
    _, lookup, _ = role_lookup
    monkeypatch.setattr(admin_auth, "get_domino_api_host", lambda: None)

    with pytest.raises(RuntimeError):
        lookup()


# ----- preview examples -----

def test_load_preview_examples_ignores_unknown_tools_with_a_warning(tmp_path, monkeypatch, caplog):
    path = tmp_path / "examples.json"
    path.write_text(json.dumps({"known": {"x": 1}, "gone": {"y": 2}, "bad": [1, 2]}))
    monkeypatch.setattr(catalog, "PREVIEW_EXAMPLES_PATH", path)

    with caplog.at_level("WARNING"):
        examples = catalog.load_preview_examples(["known", "bad", "other"])

    assert examples == {"known": {"x": 1}}
    assert "gone" in caplog.text


@pytest.mark.parametrize("content", [None, "{not json", "[1]"])
def test_load_preview_examples_is_optional_and_never_raises(tmp_path, monkeypatch, content):
    path = tmp_path / "examples.json"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr(catalog, "PREVIEW_EXAMPLES_PATH", path)

    assert catalog.load_preview_examples(["known"]) == {}
