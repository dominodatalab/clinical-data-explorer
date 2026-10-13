"""Unit tests for chat agent helpers."""

import asyncio
import importlib
from types import SimpleNamespace

from cachetools import LRUCache
import pytest
from pydantic_ai.messages import ModelResponse, TextPart

import chat_agent


@pytest.fixture(autouse=True)
def reset_chat_agent_state(monkeypatch, tmp_path):
    # Never read a real policy file (developer workspaces on Domino mount datasets).
    monkeypatch.setenv("CHAT_TOOL_POLICY_PATH", str(tmp_path / "chat_tool_policy.json"))
    for env_var in (
        "CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B",
        "CHAT_AGENT_MESSAGE_HISTORY_CAP",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "LLM_MODEL",
        "DOMINO_LLM_GATEWAY_URL",
        "DOMINO_LLM_GATEWAY_MODEL",
        "DOMINO_LLM_GATEWAY_TOKEN_URL",
        "API_KEY_OVERRIDE",
    ):
        monkeypatch.delenv(env_var, raising=False)

    chat_agent.chat_agent_message_cache.clear_cache()
    chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B = 500 * 1024 * 1024
    chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP = 100
    chat_agent._llm_model = None
    yield
    chat_agent.chat_agent_message_cache.clear_cache()
    chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B = 500 * 1024 * 1024
    chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP = 100
    chat_agent._llm_model = None


class FakeResult:
    def __init__(self, output, new_messages):
        self.output = output
        self._new_messages = new_messages

    def new_messages(self):
        return self._new_messages


class FakeAgent:
    def __init__(self, result, enter_error=None):
        self.result = result
        self.enter_error = enter_error
        self.run_calls = []

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def run(self, message, message_history=None):
        self.run_calls.append(
            {
                "message": message,
                "message_history": message_history,
                "message_history_snapshot": (
                    list(message_history) if message_history is not None else None
                ),
            }
        )
        return self.result


class RecordingLock:
    def __init__(self):
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1

    def __exit__(self, exc_type, exc, tb):
        return None


class UserPromptPart:
    def __init__(self, content):
        self.content = content


class FakeTextPart:
    def __init__(self, content):
        self.content = content


FakeTextPart.__name__ = "TextPart"


class FakeMessage:
    def __init__(self, parts):
        self.parts = parts


def _message(text):
    return ModelResponse(parts=[TextPart(content=text)])


def _message_text(message):
    return message.parts[0].content


def test_get_llm_config_uses_defaults_and_openai_key_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    assert chat_agent.get_llm_config() == (
        "openai",
        "https://api.openai.com/v1",
        "openai-key",
        "gpt-4o-mini",
    )


def test_get_llm_config_prefers_llm_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    assert chat_agent.get_llm_config() == (
        "openai",
        "https://llm.example/v1",
        "llm-key",
        "test-model",
    )


def test_is_chat_configured_requires_api_key_for_remote_provider(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    assert chat_agent.is_chat_configured() is False

    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    assert chat_agent.is_chat_configured() is True


def test_is_chat_configured_allows_local_provider_without_api_key(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3")

    assert chat_agent.is_chat_configured() is True


def test_get_chat_status_hides_remote_config_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    assert chat_agent.get_chat_status() == {
        "configured": False,
        "provider": "openai",
        "base_url": None,
        "model": None,
        "is_local": False,
        "missing": ["LLM_API_KEY"],
    }


def test_message_history_cap_defaults_to_100(monkeypatch):
    monkeypatch.delenv("CHAT_AGENT_MESSAGE_HISTORY_CAP", raising=False)

    importlib.reload(chat_agent.chat_agent_config)

    assert chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP == 100


def test_message_history_cap_can_be_set_from_environment(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_MESSAGE_HISTORY_CAP", "7")

    importlib.reload(chat_agent.chat_agent_config)

    assert chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP == 7


def test_message_history_cache_size_defaults_to_500mb(monkeypatch):
    monkeypatch.delenv("CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B", raising=False)

    importlib.reload(chat_agent.chat_agent_config)

    assert (
        chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B
        == 500 * 1024 * 1024
    )


def test_message_history_cache_size_can_be_set_from_environment(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B", "12345")

    importlib.reload(chat_agent.chat_agent_config)

    assert chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B == 12345


def test_message_history_cache_uses_configured_size(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B", "12345")

    importlib.reload(chat_agent.chat_agent_config)
    importlib.reload(chat_agent.chat_agent_message_cache)

    try:
        assert chat_agent.get_message_histories().maxsize == 12345
    finally:
        monkeypatch.delenv("CHAT_AGENT_MESSAGE_HISTORY_CACHE_SIZE_B", raising=False)
        importlib.reload(chat_agent.chat_agent_config)
        importlib.reload(chat_agent.chat_agent_message_cache)


def test_get_message_histories_returns_singleton():
    chat_agent.chat_agent_message_cache.clear_cache()
    try:
        first = chat_agent.get_message_histories()
        second = chat_agent.get_message_histories()

        assert first is second
        assert isinstance(first, LRUCache)
        assert first.maxsize == 500 * 1024 * 1024

        first["session-1"] = []
        assert second == {"session-1": []}
    finally:
        chat_agent.chat_agent_message_cache.clear_cache()


def test_message_history_cache_operations_use_lock(monkeypatch):
    lock = RecordingLock()
    monkeypatch.setattr(chat_agent.chat_agent_message_cache, "_cache_lock", lock)

    chat_agent.chat_agent_message_cache.add_messages("session-1", ["message-1"])
    chat_agent.chat_agent_message_cache.get_messages("session-1")
    chat_agent.chat_agent_message_cache.clear_messages("session-1")
    chat_agent.chat_agent_message_cache.clear_cache()

    assert lock.enter_count == 4


def test_clear_history_removes_only_requested_session():
    histories = chat_agent.get_message_histories()
    histories["session-1"] = ["message-1"]
    histories["session-2"] = ["message-2"]

    chat_agent.clear_history("session-1")

    assert histories == {"session-2": ["message-2"]}


def test_get_history_returns_ui_transcript_with_chart_payloads():
    chat_agent.chat_agent_message_cache.add_messages(
        "session-history",
        [
            FakeMessage([UserPromptPart("show me counts")]),
            FakeMessage([
                FakeTextPart(
                    'Here you go [CHART_DATA]{"type":"bar","data":{"categories":["A"],"values":[1]}}[/CHART_DATA]'
                )
            ]),
            FakeMessage([object()]),
        ],
    )

    assert chat_agent.get_history("session-history") == [
        {"sender": "user", "text": "show me counts"},
        {
            "sender": "agent",
            "text": "Here you go",
            "charts": [{"type": "bar", "data": {"categories": ["A"], "values": [1]}}],
        },
    ]


def test_get_agent_response_raises_when_chat_is_not_configured():
    with pytest.raises(RuntimeError, match="Chat is not configured"):
        asyncio.run(chat_agent.get_agent_response("hello", session_id="session-1"))


def test_get_agent_response_parses_charts_and_updates_message_history(monkeypatch):
    existing_history = chat_agent.chat_agent_message_cache.add_messages(
        "session-1", ["old-message"]
    )
    result = FakeResult(
        'Here is a chart [CHART_DATA]{"type": "bar", "x": "age"}[/CHART_DATA]',
        ["new-message"],
    )
    agent = FakeAgent(result)

    monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_agent, "_create_agent_for_session", lambda session_id, authorization_header=None: agent)

    response = asyncio.run(
        chat_agent.get_agent_response("show me age", session_id="session-1")
    )

    assert response == {
        "text": "Here is a chart",
        "charts": [{"type": "bar", "x": "age"}],
        "user_only_results": [],
    }
    assert agent.run_calls == [
        {
            "message": "show me age",
            "message_history": existing_history,
            "message_history_snapshot": ["old-message"],
        }
    ]
    assert chat_agent.get_message_histories()["session-1"] == [
        "old-message",
        "new-message",
    ]


def test_extract_response_payload_warns_when_chart_json_is_malformed():
    valid_chart = {
        "type": "bar",
        "title": "Counts",
        "data": {"categories": ["A"], "values": [1]},
    }
    response_text = """
Summary before the charts.
[CHART_DATA]
{"type": "bar", "title": "Counts", "data": {"categories": ["A"], "values": [1]}}
[/CHART_DATA]
Another note.
[CHART_DATA]
{"type": "bar", "data":
[/CHART_DATA]
"""

    payload = chat_agent._extract_response_payload(response_text)

    assert payload["charts"] == [valid_chart]
    assert "Summary before the charts." in payload["text"]
    assert "Another note." in payload["text"]
    assert chat_agent.MALFORMED_CHART_WARNING in payload["text"]
    assert "[CHART_DATA]" not in payload["text"]


def test_extract_response_payload_strips_fence_wrapped_chart_block():
    response_text = (
        "Here is the breakdown.\n"
        "```json\n"
        '[CHART_DATA]\n{"type": "bar", "data": {"categories": ["A"], "values": [1]}}\n[/CHART_DATA]\n'
        "```\n"
        "Done."
    )

    payload = chat_agent._extract_response_payload(response_text)

    assert payload["charts"] == [{"type": "bar", "data": {"categories": ["A"], "values": [1]}}]
    assert "```" not in payload["text"]
    assert "Here is the breakdown." in payload["text"]
    assert "Done." in payload["text"]


def test_extract_response_payload_parses_two_chart_blocks():
    response_text = (
        "First chart.\n"
        '[CHART_DATA]{"type": "bar", "data": {}}[/CHART_DATA]\n'
        "Second chart.\n"
        '[CHART_DATA]{"type": "pie", "data": {}}[/CHART_DATA]\n'
    )

    payload = chat_agent._extract_response_payload(response_text)

    assert [chart["type"] for chart in payload["charts"]] == ["bar", "pie"]
    assert "First chart." in payload["text"]
    assert "Second chart." in payload["text"]


def test_extract_response_payload_drops_unterminated_chart_block():
    response_text = (
        "Here is the distribution.\n"
        '[CHART_DATA]\n{"type": "bar", "data": {"categories": ["A", "B"'
    )

    payload = chat_agent._extract_response_payload(response_text)

    assert payload["charts"] == []
    assert "[CHART_DATA]" not in payload["text"]
    assert "categories" not in payload["text"]
    assert chat_agent.TRUNCATED_CHART_WARNING in payload["text"]
    assert "Here is the distribution." in payload["text"]


def test_extract_response_payload_collapses_blank_line_runs_left_by_chart_removal():
    response_text = (
        "Text A.\n\n\n\n"
        '[CHART_DATA]{"type": "bar", "data": {}}[/CHART_DATA]'
        "\n\n\n\nText B."
    )

    payload = chat_agent._extract_response_payload(response_text)

    assert payload["text"] == "Text A.\n\nText B."


def test_extract_response_payload_returns_text_unchanged_when_no_chart_present():
    response_text = "Just a plain text answer with no charts at all."

    payload = chat_agent._extract_response_payload(response_text)

    assert payload == {"text": response_text, "charts": []}


def test_get_agent_response_keeps_chart_payloads_in_message_history(monkeypatch):
    output = (
        "Here is the distribution.\n"
        "[CHART_DATA]"
        '{"type":"bar","title":"Counts","data":{"categories":["A"],"values":[1]}}'
        "[/CHART_DATA]"
    )
    result = FakeResult(output, [_message(output)])
    agent = FakeAgent(result)

    monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_agent, "_create_agent_for_session", lambda session_id, authorization_header=None: agent)

    response = asyncio.run(
        chat_agent.get_agent_response("show a chart", session_id="session-chart")
    )

    assert response["charts"][0]["type"] == "bar"
    assert response["text"] == "Here is the distribution."
    stored_text = _message_text(chat_agent.get_message_histories()["session-chart"][0])
    assert stored_text == output
    assert "[CHART_DATA]" in stored_text
    assert "categories" in stored_text


def test_get_agent_response_caps_message_history(monkeypatch):
    existing_history = chat_agent.chat_agent_message_cache.add_messages(
        "session-1", ["old-0", "old-1", "old-2", "old-3"]
    )
    chat_agent.chat_agent_config.CHAT_AGENT_MESSAGE_HISTORY_CAP = 3
    result = FakeResult("plain response", ["new-1", "new-2"])
    agent = FakeAgent(result)

    monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_agent, "_create_agent_for_session", lambda session_id, authorization_header=None: agent)

    response = asyncio.run(
        chat_agent.get_agent_response("hello", session_id="session-1")
    )

    assert response == {"text": "plain response", "charts": [], "user_only_results": []}
    assert agent.run_calls == [
        {
            "message": "hello",
            "message_history": existing_history,
            "message_history_snapshot": ["old-1", "old-2", "old-3"],
        }
    ]
    assert chat_agent.get_message_histories()["session-1"] == [
        "old-3",
        "new-1",
        "new-2",
    ]


def test_get_agent_response_falls_back_when_mcp_context_fails(monkeypatch):
    result = FakeResult("plain response", ["new-message"])
    mcp_agent = FakeAgent(result, enter_error=RuntimeError("mcp unavailable"))
    fallback_agent = FakeAgent(result)

    monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_agent, "_create_agent_for_session", lambda session_id, authorization_header=None: mcp_agent)
    monkeypatch.setattr(chat_agent, "_create_agent_without_mcp", lambda: fallback_agent)

    response = asyncio.run(
        chat_agent.get_agent_response("hello", session_id="session-1")
    )

    assert response == {"text": "plain response", "charts": [], "user_only_results": []}
    assert mcp_agent.run_calls == []
    assert fallback_agent.run_calls == [
        {
            "message": "hello",
            "message_history": ["new-message"],
            "message_history_snapshot": [],
        }
    ]
    assert chat_agent.get_message_histories()["session-1"] == ["new-message"]


def test_create_agent_for_session_uses_authorization_header_for_mcp_connection(monkeypatch):
    """When a real JWT authorization_header is provided it must be forwarded to the MCP
    server verbatim so the server can call /api/users/v1/self and derive the user ID.
    Passing the user ID as a fake Bearer token (the pre-fix behaviour) would cause a 401."""
    captured = {}

    class CapturingMCPServerSSE:
        def __init__(self, url, headers):
            captured["headers"] = headers

    monkeypatch.setattr(chat_agent, "MCPServerSSE", CapturingMCPServerSSE)
    monkeypatch.setattr(chat_agent, "_get_llm_model", lambda: object())
    monkeypatch.setattr(chat_agent, "Agent", lambda model, **kwargs: SimpleNamespace(**kwargs))

    chat_agent._create_agent_for_session("user-id-123", authorization_header="Bearer jwt-token-xyz")

    assert captured["headers"] == {"Authorization": "Bearer jwt-token-xyz"}


def test_create_agent_for_session_falls_back_to_session_id_when_no_authorization_header(monkeypatch):
    """When no authorization_header is provided (local / dev mode) the session ID is used
    as the Bearer token, preserving backward-compatible local development behaviour."""
    captured = {}

    class CapturingMCPServerSSE:
        def __init__(self, url, headers):
            captured["headers"] = headers

    monkeypatch.setattr(chat_agent, "MCPServerSSE", CapturingMCPServerSSE)
    monkeypatch.setattr(chat_agent, "_get_llm_model", lambda: object())
    monkeypatch.setattr(chat_agent, "Agent", lambda model, **kwargs: SimpleNamespace(**kwargs))

    chat_agent._create_agent_for_session("user-id-123")

    assert captured["headers"] == {"Authorization": "Bearer user-id-123"}


def test_get_agent_response_passes_authorization_header_to_agent_creation(monkeypatch):
    """authorization_header received by get_agent_response must be forwarded to
    _create_agent_for_session so the MCP connection carries the real JWT rather
    than the Domino user ID."""
    captured = {}
    result = FakeResult("response", [])

    def capturing_create(session_id, authorization_header=None):
        captured["session_id"] = session_id
        captured["authorization_header"] = authorization_header
        return FakeAgent(result)

    monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
    monkeypatch.setattr(chat_agent, "_create_agent_for_session", capturing_create)

    asyncio.run(
        chat_agent.get_agent_response(
            "hello",
            session_id="user-id-123",
            authorization_header="Bearer jwt-token-xyz",
        )
    )

    assert captured["session_id"] == "user-id-123"
    assert captured["authorization_header"] == "Bearer jwt-token-xyz"


# ----- chat tool policy wiring -----

import json  # noqa: E402

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart as RealUserPromptPart,
)
from pydantic_ai.models.function import FunctionModel  # noqa: E402
from pydantic_ai.toolsets import FunctionToolset  # noqa: E402

from backend.chat_tools.policy import get_policy_store  # noqa: E402
from backend.chat_tools.toolset import (  # noqa: E402
    HIDDEN_RESULT_METADATA_KEY,
    USER_ONLY_PLACEHOLDER,
)

SECRET = 42.42


def _write_policy(tmp_path, tools, default_mode="enabled"):
    (tmp_path / "chat_tool_policy.json").write_text(json.dumps({
        "default_mode": default_mode,
        "tools": {name: {"mode": mode} for name, mode in tools.items()},
    }))


class _ScriptedModel:
    """FunctionModel callable: makes the given tool calls, one per request, then answers."""

    def __init__(self, calls):
        self.calls = list(calls)
        self.step = 0
        self.requests = []
        self.tool_names = []

    def respond(self, messages, info):
        from pydantic_ai.messages import ModelResponse as Resp, TextPart as Text

        self.requests.append(list(messages))
        self.tool_names.append([tool.name for tool in info.function_tools])
        if self.step < len(self.calls):
            name, args = self.calls[self.step]
            self.step += 1
            return Resp(parts=[ToolCallPart(name, args, tool_call_id=f"call-{self.step}")])
        return Resp(parts=[Text("All done")])

    def serialized(self):
        chunks = []
        for request in self.requests:
            for message in request:
                if isinstance(message, ModelRequest):
                    for part in message.parts:
                        if isinstance(part, ToolReturnPart):
                            chunks.append(part.model_response_str())
        return "\n".join(chunks)


@pytest.fixture
def wired_agent(monkeypatch):
    """Real Agent + real policy store; FunctionModel instead of an LLM, FunctionToolset instead of MCP."""
    def install(calls):
        model = _ScriptedModel(calls)
        data_tools = FunctionToolset()

        @data_tools.tool
        def mean_age() -> dict:
            """Mean Age"""
            return {"mean": SECRET}

        @data_tools.tool
        def head(rows: int = 2) -> list:
            """Head"""
            return [{"row": i, "value": SECRET} for i in range(rows)]

        @data_tools.tool
        def fails() -> str:
            """Fails"""
            from pydantic_ai import ModelRetry
            raise ModelRetry('Error calling fails. Status code: 400. Response: {"detail": "bad value Placebo"}')

        monkeypatch.setattr(chat_agent, "is_chat_configured", lambda: True)
        monkeypatch.setattr(chat_agent, "_get_llm_model", lambda: FunctionModel(model.respond))
        monkeypatch.setattr(chat_agent, "MCPServerSSE", lambda url, headers: data_tools)
        return model

    return install


def test_user_only_results_are_returned_to_the_user_and_hidden_from_the_model(wired_agent, tmp_path):
    _write_policy(tmp_path, {"mean_age": "user_only"})
    model = wired_agent([("mean_age", {}), ("head", {"rows": 1})])

    response = asyncio.run(chat_agent.get_agent_response("go", session_id="s-hidden"))

    assert response["text"] == "All done"
    assert [r["tool_name"] for r in response["user_only_results"]] == ["mean_age"]
    hidden = response["user_only_results"][0]
    assert hidden == {
        "tool_call_id": "call-1",
        "tool_name": "mean_age",
        "title": "Mean Age",
        "args": {},
        "content": {"mean": SECRET},
        "truncated": False,
        "size_bytes": hidden["size_bytes"],
    }
    # `head` is still On: the model saw its output; mean_age's never reached it.
    assert "[{\"row\":0,\"value\":42.42}]" in model.serialized().replace(" ", "")
    assert str({"mean": SECRET}).replace(" ", "") not in model.serialized().replace(" ", "")
    assert '"mean"' not in model.serialized()
    assert USER_ONLY_PLACEHOLDER.format(tool="mean_age") in model.serialized()


def test_disabled_tool_is_not_offered_through_the_real_agent(wired_agent, tmp_path):
    _write_policy(tmp_path, {"head": "disabled"})
    model = wired_agent([])

    asyncio.run(chat_agent.get_agent_response("go", session_id="s-disabled"))

    assert sorted(model.tool_names[0]) == ["fails", "mean_age"]


def test_user_only_instructions_reach_the_model_only_when_needed(wired_agent, tmp_path):
    _write_policy(tmp_path, {"mean_age": "user_only"})
    captured = []
    model = wired_agent([])
    original = model.respond

    def respond(messages, info):
        captured.append(info.instructions)
        return original(messages, info)

    chat_agent._get_llm_model = lambda: FunctionModel(respond)

    asyncio.run(chat_agent.get_agent_response("go", session_id="s-instr"))

    assert "- mean_age" in captured[0]
    assert "head" not in captured[0]


def test_stored_history_keeps_hidden_result_and_get_history_interleaves_it(wired_agent, tmp_path):
    _write_policy(tmp_path, {"mean_age": "user_only"})
    wired_agent([("mean_age", {})])

    asyncio.run(chat_agent.get_agent_response("how old?", session_id="s-hist"))
    transcript = chat_agent.get_history("s-hist")

    assert [entry["sender"] for entry in transcript] == ["user", "tool_result", "agent"]
    assert transcript[0]["text"] == "how old?"
    assert transcript[1]["tool_name"] == "mean_age"
    assert transcript[1]["content"] == {"mean": SECRET}
    assert transcript[1]["title"] == "Mean Age"
    assert transcript[2]["text"] == "All done"


def test_policy_change_redacts_earlier_outputs_from_the_next_turn(wired_agent, tmp_path):
    model = wired_agent([("mean_age", {})])
    asyncio.run(chat_agent.get_agent_response("first", session_id="s-switch"))
    assert '"mean"' in model.serialized()  # On: the model saw the output

    _write_policy(tmp_path, {"mean_age": "user_only"})
    second = wired_agent([])
    asyncio.run(chat_agent.get_agent_response("second", session_id="s-switch"))

    assert '"mean"' not in second.serialized()
    assert USER_ONLY_PLACEHOLDER.format(tool="mean_age") in second.serialized()
    # the UI transcript still shows the real earlier result via the cached originals
    stored = chat_agent.chat_agent_message_cache.get_messages("s-switch")
    assert any(
        isinstance(part, ToolReturnPart) and part.content == {"mean": SECRET}
        for message in stored
        for part in getattr(message, "parts", [])
    )


def test_oversized_hidden_result_is_truncated_at_the_cap(wired_agent, tmp_path, monkeypatch):
    _write_policy(tmp_path, {"head": "user_only"})
    monkeypatch.setenv("CHAT_USER_ONLY_RESULT_MAX_BYTES", "1500")
    wired_agent([("head", {"rows": 300})])

    response = asyncio.run(chat_agent.get_agent_response("go", session_id="s-cap"))

    hidden = response["user_only_results"][0]
    assert hidden["truncated"] is True
    assert hidden["size_bytes"] > 1500
    assert hidden["content"]["truncated"] is True
    assert len(hidden["content"]["preview"]) < 300


def test_failed_hidden_call_shows_the_user_the_full_error_and_the_model_a_safe_one(wired_agent, tmp_path):
    _write_policy(tmp_path, {"fails": "user_only"})
    model = wired_agent([("fails", {})])

    response = asyncio.run(chat_agent.get_agent_response("go", session_id="s-err"))

    [card] = response["user_only_results"]
    assert card["is_error"] is True
    assert card["tool_name"] == "fails"
    assert "Placebo" in card["error"]
    retry_texts = [
        part.model_response()
        for request in model.requests
        for message in request
        if isinstance(message, ModelRequest)
        for part in message.parts
        if hasattr(part, "model_response") and type(part).__name__ == "RetryPromptPart"
    ]
    assert retry_texts and all("Placebo" not in text for text in retry_texts)


def test_extract_user_only_results_follows_tool_call_order_and_ignores_other_parts():
    def ret(call_id, name):
        return ModelRequest(parts=[ToolReturnPart(name, "placeholder", tool_call_id=call_id, metadata={
            HIDDEN_RESULT_METADATA_KEY: {"tool_name": name, "args": {}, "content": [name], "truncated": False, "size_bytes": 3}
        })])

    plain = ModelRequest(parts=[ToolReturnPart("plain", {"x": 1}, tool_call_id="c3")])
    messages = [
        ModelRequest(parts=[RealUserPromptPart("hi")]),
        ModelResponse(parts=[ToolCallPart("b", {}, tool_call_id="c1"), ToolCallPart("a", {}, tool_call_id="c2")]),
        ret("c2", "a"),
        plain,
        ret("c1", "b"),
        "not-a-message",
    ]

    results = chat_agent._extract_user_only_results(messages, hidden_errors=[
        {"tool_call_id": "c9", "tool_name": "z", "args": {"q": 1}, "error": "boom"},
    ])

    assert [r["tool_name"] for r in results] == ["b", "a", "z"]
    assert results[0]["title"] == "b"  # falls back to the tool name
    assert results[2] == {
        "tool_call_id": "c9", "tool_name": "z", "title": "z", "args": {"q": 1}, "is_error": True, "error": "boom",
    }


def test_invalid_policy_runs_without_mcp_and_returns_a_notice(wired_agent, tmp_path, monkeypatch):
    (tmp_path / "chat_tool_policy.json").write_text("{corrupt")
    model = wired_agent([])
    built = []
    monkeypatch.setattr(chat_agent, "MCPServerSSE", lambda url, headers: built.append(url))

    response = asyncio.run(chat_agent.get_agent_response("go", session_id="s-invalid"))

    assert built == []  # MCP never connected: fail closed
    assert model.tool_names == [[]]  # the model was offered no tools at all
    assert response["notice"] == chat_agent.POLICY_UNAVAILABLE_NOTICE
    assert response["user_only_results"] == []


def test_valid_policy_has_no_notice(wired_agent, tmp_path):
    _write_policy(tmp_path, {})
    wired_agent([])

    response = asyncio.run(chat_agent.get_agent_response("go", session_id="s-ok"))

    assert "notice" not in response


def test_missing_policy_file_uses_built_in_defaults(wired_agent):
    model = wired_agent([])

    asyncio.run(chat_agent.get_agent_response("go", session_id="s-defaults"))

    # No file: defaults apply (only get_dataset_data is off, and it isn't in this stub catalog).
    assert sorted(model.tool_names[0]) == ["fails", "head", "mean_age"]
