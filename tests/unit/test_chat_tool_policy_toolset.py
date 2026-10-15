"""Enforcement tests for ChatToolPolicyToolset.

Uses pydantic-ai's FunctionModel + FunctionToolset, so no network calls: the
FunctionModel records every request it is sent, which lets the tests assert on
exactly what a real model would have seen.
"""
import json
from dataclasses import replace

import pytest
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from backend.chat_tools.policy import ChatToolPolicy, ToolMode, ToolPolicyEntry
from backend.chat_tools.toolset import (
    DISABLED_REDACTION,
    HIDDEN_RESULT_METADATA_KEY,
    USER_ONLY_DESCRIPTION_PREFIX,
    USER_ONLY_ERROR_GENERIC,
    USER_ONLY_ERROR_NO_STATUS,
    USER_ONLY_PLACEHOLDER,
    ChatToolPolicyToolset,
    build_policy_instructions,
    cap_hidden_result,
    make_policy_history_processor,
    model_safe_error_message,
    tool_title,
)

SECRET = 42.42

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _policy(default="enabled", **modes):
    return ChatToolPolicy(
        default_mode=ToolMode(default),
        tools={name: ToolPolicyEntry(mode=ToolMode(mode)) for name, mode in modes.items()},
    )


def _base_toolset():
    toolset = FunctionToolset()

    @toolset.tool
    def mean_age() -> dict:
        """Mean Age

        Returns the mean age."""
        return {"mean": SECRET}

    @toolset.tool
    def head(rows: int = 3) -> list:
        """Head"""
        return [{"row": i, "value": SECRET} for i in range(rows)]

    @toolset.tool
    def whole_dataset() -> list:
        """Whole dataset"""
        return [{"x": 1}]

    @toolset.tool
    def fails() -> str:
        """Fails"""
        raise ModelRetry(
            'Error calling fails. Status code: 400. Response: {"detail":"Could not parse dates: Placebo"}'
        )

    return toolset


class Recorder:
    """A FunctionModel that makes the given tool calls one per request, then answers."""

    def __init__(self, calls):
        self.calls = list(calls)
        self.step = 0
        self.requests: list[list] = []  # messages of every model request
        self.tool_names: list[list[str]] = []  # tools offered on every model request

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self.respond)

    def respond(self, messages, info: AgentInfo):
        self.requests.append(list(messages))
        self.tool_names.append([tool.name for tool in info.function_tools])
        if self.step < len(self.calls):
            name, args = self.calls[self.step]
            self.step += 1
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart("done")])

    def seen_by_model(self) -> str:
        """Everything the model received, serialized: tool returns, retries, prompts."""
        chunks = []
        for request in self.requests:
            for message in request:
                if isinstance(message, ModelRequest):
                    for part in message.parts:
                        if isinstance(part, (ToolReturnPart,)):
                            chunks.append(part.model_response_str())
                        elif isinstance(part, RetryPromptPart):
                            chunks.append(part.model_response())
                        elif isinstance(part, UserPromptPart):
                            chunks.append(str(part.content))
        return "\n".join(chunks)


def _agent(recorder, policy, **toolset_kwargs):
    toolset = ChatToolPolicyToolset(wrapped=_base_toolset(), policy=policy, **toolset_kwargs)
    agent = Agent(recorder.model, toolsets=[toolset], retries=2)
    return agent, toolset


def _tool_return_parts(result):
    return [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


# ----- layer 1: advertisement -----

async def test_disabled_tool_is_never_offered_to_the_model():
    recorder = Recorder([])
    agent, _ = _agent(recorder, _policy(whole_dataset="disabled"))

    await agent.run("hi")

    assert sorted(recorder.tool_names[0]) == ["fails", "head", "mean_age"]


async def test_default_mode_disabled_hides_tools_without_an_entry():
    recorder = Recorder([])
    agent, _ = _agent(recorder, _policy(default="disabled", head="enabled"))

    await agent.run("hi")

    assert recorder.tool_names[0] == ["head"]


async def test_user_only_description_is_prefixed_and_enabled_is_untouched():
    descriptions = {}

    def model(messages, info):
        descriptions.update({tool.name: tool.description for tool in info.function_tools})
        return ModelResponse(parts=[TextPart("ok")])

    toolset = ChatToolPolicyToolset(wrapped=_base_toolset(), policy=_policy(mean_age="user_only"))
    await Agent(FunctionModel(model), toolsets=[toolset]).run("hi")

    assert descriptions["mean_age"].startswith(USER_ONLY_DESCRIPTION_PREFIX)
    assert "Returns the mean age." in descriptions["mean_age"]
    assert descriptions["head"] == "Head"


# ----- layer 2: execution -----

async def test_enabled_tool_is_unchanged():
    recorder = Recorder([("mean_age", {})])
    agent, toolset = _agent(recorder, _policy())

    result = await agent.run("hi")

    part = _tool_return_parts(result)[0]
    assert part.content == {"mean": SECRET}
    assert part.metadata is None
    assert HIDDEN_RESULT_METADATA_KEY not in str(part.metadata)


async def test_user_only_output_never_reaches_the_model():
    recorder = Recorder([("mean_age", {}), ("head", {"rows": 2})])
    agent, _ = _agent(recorder, _policy(mean_age="user_only", head="user_only"))

    result = await agent.run("hi")

    # What every model adapter serializes (`content`, never `metadata`):
    assert str(SECRET) not in recorder.seen_by_model()
    for request in recorder.requests:
        for message in request:
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart):
                    assert str(SECRET) not in json.dumps(part.model_response_object())
    parts = _tool_return_parts(result)
    assert [p.content for p in parts] == [
        USER_ONLY_PLACEHOLDER.format(tool="mean_age"),
        USER_ONLY_PLACEHOLDER.format(tool="head"),
    ]


async def test_user_only_metadata_holds_the_raw_result():
    recorder = Recorder([("head", {"rows": 2})])
    agent, _ = _agent(recorder, _policy(head="user_only"))

    result = await agent.run("hi")

    hidden = _tool_return_parts(result)[0].metadata[HIDDEN_RESULT_METADATA_KEY]
    assert hidden["tool_name"] == "head"
    assert hidden["title"] == "Head"
    assert hidden["args"] == {"rows": 2}
    assert hidden["content"] == [{"row": 0, "value": SECRET}, {"row": 1, "value": SECRET}]
    assert hidden["truncated"] is False
    assert hidden["size_bytes"] > 0


async def test_title_comes_from_first_description_line():
    recorder = Recorder([("mean_age", {})])
    agent, _ = _agent(recorder, _policy(mean_age="user_only"))

    result = await agent.run("hi")

    assert _tool_return_parts(result)[0].metadata[HIDDEN_RESULT_METADATA_KEY]["title"] == "Mean Age"


async def test_oversized_user_only_result_is_capped_before_it_is_stored():
    recorder = Recorder([("head", {"rows": 500})])
    agent, _ = _agent(recorder, _policy(head="user_only"), max_result_bytes=2000)

    result = await agent.run("hi")

    hidden = _tool_return_parts(result)[0].metadata[HIDDEN_RESULT_METADATA_KEY]
    assert hidden["truncated"] is True
    assert hidden["content"]["truncated"] is True
    assert hidden["content"]["size_bytes"] == hidden["size_bytes"] > 2000
    assert 0 < len(hidden["content"]["preview"]) < 500
    assert len(json.dumps(hidden["content"])) <= 2000


async def test_disabled_tool_call_is_refused_even_if_the_model_gets_past_get_tools():
    toolset = ChatToolPolicyToolset(wrapped=_base_toolset(), policy=_policy(whole_dataset="disabled"))

    class Ctx:
        tool_call_id = "c1"

    with pytest.raises(ModelRetry, match="not available"):
        await toolset.call_tool("whole_dataset", {}, Ctx(), object())


# ----- errors from user_only tools (D8) -----

async def test_user_only_failure_hides_exception_text_from_model_but_not_the_user():
    recorder = Recorder([("fails", {})])
    agent, toolset = _agent(recorder, _policy(fails="user_only"))

    await agent.run("hi")

    assert "Placebo" not in recorder.seen_by_model()
    assert USER_ONLY_ERROR_GENERIC.format(status=400) in recorder.seen_by_model()
    assert toolset.hidden_errors
    first = toolset.hidden_errors[0]
    assert first["tool_name"] == "fails"
    assert "Placebo" in first["error"]
    assert first["tool_call_id"]
    assert first["title"] == "Fails"


async def test_enabled_tool_errors_pass_through_unchanged():
    recorder = Recorder([("fails", {})])
    agent, toolset = _agent(recorder, _policy())

    await agent.run("hi")

    assert "Could not parse dates: Placebo" in recorder.seen_by_model()
    assert toolset.hidden_errors == []


def _error(status, body):
    return f"Error calling t. Status code: {status}. Response: {body}"


def test_model_safe_error_passes_through_model_safe_detail():
    text = _error(400, json.dumps({"detail": "Could not parse dates: Placebo",
                                    "model_safe_detail": "Could not parse dates in column 'X'"}))

    safe = model_safe_error_message("t", text)

    assert safe == "Error calling t. Status code: 400. Could not parse dates in column 'X'"
    assert "Placebo" not in safe


def test_model_safe_error_passes_validation_errors_through():
    text = _error(422, json.dumps({"detail": [{"loc": ["query", "bins"], "msg": "bad", "input": "abc"}]}))

    assert model_safe_error_message("t", text) == text


@pytest.mark.parametrize("body", [
    json.dumps({"detail": "Error applying expression: 'Placebo' > 3"}),  # no model_safe_detail
    json.dumps({"detail": "x", "model_safe_detail": ""}),
    json.dumps({"detail": "x", "model_safe_detail": 5}),
    "not json at all: Placebo",
    "[1, 2]",
])
def test_model_safe_error_fails_closed_to_generic_message(body):
    safe = model_safe_error_message("t", _error(500, body))

    assert safe == USER_ONLY_ERROR_GENERIC.format(status=500)
    assert "Placebo" not in safe


def test_model_safe_error_without_status_is_generic():
    assert model_safe_error_message("t", "Tool 'x' blew up with Placebo") == USER_ONLY_ERROR_NO_STATUS


# ----- layer 3: history -----

def _history(tool_name="mean_age", content=None, retry=False):
    call = ModelResponse(parts=[ToolCallPart(tool_name, {}, tool_call_id="c1")])
    if retry:
        part = RetryPromptPart(content or f"leaked {SECRET}", tool_name=tool_name, tool_call_id="c1")
    else:
        part = ToolReturnPart(tool_name, content or {"mean": SECRET}, tool_call_id="c1", metadata={"raw": SECRET})
    return [
        ModelRequest(parts=[UserPromptPart("q")]),
        call,
        ModelRequest(parts=[part]),
        ModelResponse(parts=[TextPart("answer")]),
    ]


def test_history_processor_redacts_outputs_of_tools_now_user_only():
    processed = make_policy_history_processor(_policy(mean_age="user_only"))(_history())

    part = processed[2].parts[0]
    assert part.content == USER_ONLY_PLACEHOLDER.format(tool="mean_age")
    assert part.metadata is None
    assert str(SECRET) not in repr(processed[2])


def test_history_processor_redacts_outputs_of_tools_now_disabled():
    processed = make_policy_history_processor(_policy(mean_age="disabled"))(_history())

    assert processed[2].parts[0].content == DISABLED_REDACTION.format(tool="mean_age")


def test_history_processor_leaves_the_models_own_replies_alone():
    """The policy governs tool output. What the model itself wrote (replies and tool-call
    arguments) is free text we can't reliably scrub, so it passes through unchanged. A
    summary written while a tool was On therefore outlives the tool being restricted."""
    history = _history()
    history[3] = ModelResponse(parts=[TextPart(f"The mean is {SECRET}")])
    original = list(history)

    processed = make_policy_history_processor(_policy(mean_age="disabled"))(history)

    assert processed[1] is original[1]
    assert processed[3] is original[3]
    assert str(SECRET) not in repr(processed[2])  # the tool output itself is still redacted


def test_history_processor_redacts_old_errors_of_restricted_tools():
    processed = make_policy_history_processor(_policy(mean_age="user_only"))(_history(retry=True))

    assert str(SECRET) not in repr(processed[2])


def test_history_processor_leaves_enabled_tools_alone():
    history = _history()

    processed = make_policy_history_processor(_policy())(history)

    assert processed[2] is history[2]


def test_history_processor_does_not_mutate_its_input():
    history = _history()
    snapshot = [replace(m) if isinstance(m, ModelRequest) else m for m in history]
    original_part = history[2].parts[0]

    processed = make_policy_history_processor(_policy(mean_age="user_only"))(history)

    assert processed is not history
    assert history[2].parts[0] is original_part
    assert original_part.content == {"mean": SECRET}
    assert original_part.metadata == {"raw": SECRET}
    assert history[2].parts == snapshot[2].parts


async def test_policy_change_applies_to_earlier_outputs_in_the_conversation():
    first = Recorder([("mean_age", {})])
    agent, _ = _agent(first, _policy())
    run1 = await agent.run("hi")
    assert str(SECRET) in repr(first.requests)  # enabled: the model saw it

    second = Recorder([])
    toolset = ChatToolPolicyToolset(wrapped=_base_toolset(), policy=_policy(mean_age="user_only"))
    agent2 = Agent(
        second.model,
        toolsets=[toolset],
        history_processors=[make_policy_history_processor(_policy(mean_age="user_only"))],
    )
    history = run1.all_messages()
    run2 = await agent2.run("again", message_history=history)

    assert str(SECRET) not in second.seen_by_model()
    # the cached originals still hold the real output for the UI transcript
    assert any(p.content == {"mean": SECRET} for p in _tool_return_parts(run1))
    assert [p.content for p in history[2].parts] == [{"mean": SECRET}]
    assert run2.new_messages()[0].parts[0].content == "again"


# ----- instructions -----

def test_instructions_only_when_a_visible_tool_is_user_only():
    policy = _policy(mean_age="user_only", head="disabled")

    assert build_policy_instructions(policy, ["head", "fails"]) is None
    text = build_policy_instructions(policy, ["mean_age", "fails"])
    assert "- mean_age" in text
    assert "fails" not in text
    assert "fabricate" in text


def test_instructions_cover_tools_that_inherit_a_user_only_default():
    policy = _policy(default="user_only", head="enabled")

    text = build_policy_instructions(policy, ["head", "other"])

    assert "- other" in text and "- head" not in text


# ----- size cap -----

def test_cap_leaves_small_results_alone():
    assert cap_hidden_result({"a": 1}, 1000) == {"content": {"a": 1}, "truncated": False, "size_bytes": 8}


def test_cap_keeps_first_rows_of_a_list():
    rows = [{"i": i, "pad": "x" * 50} for i in range(1000)]

    capped = cap_hidden_result(rows, 5000)

    assert capped["truncated"] is True
    preview = capped["content"]["preview"]
    assert preview == rows[: len(preview)]
    assert 0 < len(preview) < 1000
    assert len(json.dumps(capped["content"])) <= 5000


def test_cap_shrinks_the_largest_list_inside_a_dict_and_keeps_the_rest():
    result = {"total_rows": 1000, "data": [{"i": i, "pad": "x" * 50} for i in range(1000)]}

    capped = cap_hidden_result(result, 5000)

    preview = capped["content"]["preview"]
    assert preview["total_rows"] == 1000
    assert 0 < len(preview["data"]) < 1000


def test_cap_cuts_text_when_there_is_nothing_to_drop():
    capped = cap_hidden_result("é" * 5000, 1000)

    assert capped["truncated"] is True
    assert isinstance(capped["content"]["preview"], str)
    assert len(capped["content"]["preview"].encode()) <= 1000
    assert capped["size_bytes"] > 1000


def test_cap_cuts_text_for_a_single_oversized_row():
    capped = cap_hidden_result([{"blob": "y" * 10000}], 1000)

    assert capped["truncated"] is True
    assert len(json.dumps(capped["content"])) <= 1200


def test_tool_title_uses_first_line_and_falls_back_to_name():
    assert tool_title("t", "Get Feature Stats\n\nlong text") == "Get Feature Stats"
    assert tool_title("t", "\n  \n") == "t"
    assert tool_title("t", None) == "t"


async def test_hidden_result_survives_the_history_processor_within_a_run():
    """The processor runs before every model request in the run; it must not strip the
    hidden result that the app extracts from `new_messages()` afterwards."""
    policy = _policy(mean_age="user_only")
    recorder = Recorder([("mean_age", {})])
    toolset = ChatToolPolicyToolset(wrapped=_base_toolset(), policy=policy)
    agent = Agent(
        recorder.model, toolsets=[toolset], history_processors=[make_policy_history_processor(policy)]
    )

    result = await agent.run("hi")

    parts = [
        part
        for message in result.new_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert parts[0].metadata[HIDDEN_RESULT_METADATA_KEY]["content"] == {"mean": SECRET}
    assert str(SECRET) not in recorder.seen_by_model()
