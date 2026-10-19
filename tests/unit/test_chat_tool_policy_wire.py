"""What actually goes over the wire to the LLM provider.

The other policy tests check what pydantic-ai hands to a model. These go one step further and
capture the HTTP request body `OpenAIChatModel` (the class `chat_agent` uses, for the Domino LLM
Gateway too) builds, so an upgrade that started serializing `ToolReturnPart.metadata` (where
hidden-from-AI results are kept) or that changed how tools are offered would fail here.
"""
import json

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import FunctionToolset

from backend.chat_tools.policy import ChatToolPolicy, ToolMode, ToolPolicyEntry
from backend.chat_tools.toolset import ChatToolPolicyToolset, make_policy_history_processor

pytestmark = pytest.mark.anyio

SECRET = "SECRET-VALUE-42.4242"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _policy(**modes):
    return ChatToolPolicy.build(
        ToolMode.ENABLED, {name: ToolPolicyEntry(mode=ToolMode(mode)) for name, mode in modes.items()}
    )


def _completion(message):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class FakeProvider:
    """An OpenAI-compatible endpoint that records raw request bodies.

    Calls `tool_to_call` on the first request of each run, then answers with text.
    """

    def __init__(self, tool_to_call=None):
        self.tool_to_call = tool_to_call
        self.bodies: list[str] = []
        self._requests_this_run = 0

    def new_run(self):
        self._requests_this_run = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(request.content.decode())
        self._requests_this_run += 1
        if self.tool_to_call and self._requests_this_run == 1:
            return httpx.Response(200, json=_completion({
                "content": None,
                "tool_calls": [{
                    "id": f"call_{len(self.bodies)}", "type": "function",
                    "function": {"name": self.tool_to_call, "arguments": "{}"},
                }],
            }))
        return httpx.Response(200, json=_completion({"content": "All done"}))

    def model(self) -> OpenAIChatModel:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return OpenAIChatModel(
            "test-model", provider=OpenAIProvider(base_url="https://llm.example/v1", api_key="k", http_client=client)
        )

    def offered_tools(self, body_index=0) -> list[str]:
        return [tool["function"]["name"] for tool in json.loads(self.bodies[body_index]).get("tools", [])]


def _data_tools():
    toolset = FunctionToolset()

    @toolset.tool
    def mean_age() -> dict:
        """Mean Age"""
        return {"mean": SECRET}

    @toolset.tool
    def head() -> list:
        """Head"""
        return [{"value": SECRET}]

    return toolset


def _agent(provider, policy):
    return Agent(
        provider.model(),
        toolsets=[ChatToolPolicyToolset(wrapped=_data_tools(), policy=policy)],
        history_processors=[make_policy_history_processor(policy)],
    )


# ----- hidden-from-AI output never goes over the wire -----

async def test_hidden_result_never_appears_in_any_request_across_turns():
    provider = FakeProvider(tool_to_call="mean_age")
    agent = _agent(provider, _policy(mean_age="user_only"))

    first = await agent.run("what is the mean age?")
    provider.new_run()
    provider.tool_to_call = None
    await agent.run("and again?", message_history=first.all_messages())

    assert len(provider.bodies) == 3  # tool call, answer, then the follow-up turn
    assert all(SECRET not in body for body in provider.bodies), "hidden output reached the provider"
    assert "intentionally hidden from you" in provider.bodies[1]   # the placeholder is what the model got
    assert "intentionally hidden from you" in provider.bodies[2]   # ... and it is replayed from history
    # the real result is still available to the app, for the chat card
    metadata = [
        part.metadata
        for message in first.all_messages()
        for part in message.parts
        if getattr(part, "metadata", None)
    ]
    assert SECRET in json.dumps(metadata)


async def test_control_an_enabled_tool_output_does_go_over_the_wire():
    """Guards the test above: this setup *would* detect a leak."""
    provider = FakeProvider(tool_to_call="mean_age")
    agent = _agent(provider, _policy(mean_age="enabled"))

    await agent.run("what is the mean age?")

    assert SECRET in provider.bodies[1]


async def test_output_the_model_already_saw_is_redacted_once_the_tool_is_hidden():
    provider = FakeProvider(tool_to_call="mean_age")
    first = await _agent(provider, _policy(mean_age="enabled")).run("mean age?")
    assert SECRET in provider.bodies[1]

    provider.new_run()
    provider.tool_to_call = None
    await _agent(provider, _policy(mean_age="user_only")).run("again", message_history=first.all_messages())

    assert SECRET not in provider.bodies[-1]


# ----- basics: tools on and off -----

async def test_enabled_tools_are_offered_and_work():
    provider = FakeProvider(tool_to_call="head")

    result = await _agent(provider, _policy()).run("show head")

    assert sorted(provider.offered_tools(0)) == ["head", "mean_age"]
    assert SECRET in provider.bodies[1]  # the tool ran and the model received its output
    assert result.output == "All done"


async def test_disabled_tool_is_not_offered_to_the_provider():
    provider = FakeProvider()

    await _agent(provider, _policy(mean_age="disabled")).run("hi")

    assert provider.offered_tools(0) == ["head"]


async def test_all_tools_disabled_sends_no_tools_at_all():
    provider = FakeProvider()

    await _agent(provider, _policy(mean_age="disabled", head="disabled")).run("hi")

    assert json.loads(provider.bodies[0]).get("tools") in (None, [])


async def test_hidden_tool_is_offered_with_a_description_saying_its_output_is_hidden():
    provider = FakeProvider()

    await _agent(provider, _policy(mean_age="user_only")).run("hi")

    tools = {t["function"]["name"]: t["function"]["description"] for t in json.loads(provider.bodies[0])["tools"]}
    assert tools["mean_age"].startswith("[Output hidden from you]")
    assert tools["head"] == "Head"
