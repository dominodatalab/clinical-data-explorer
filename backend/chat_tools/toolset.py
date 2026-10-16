"""Enforcement of the chat tool policy, built on pydantic-ai's toolset API.

`ChatToolPolicyToolset` wraps the MCP server toolset and enforces the policy in
three places, so it does not depend on the model behaving:

1. Advertisement (`get_tools`): `disabled` tools are never offered to the model;
   `user_only` tool descriptions say their output is hidden.
2. Execution (`call_tool`): a `disabled` tool is refused (defense in depth); a
   `user_only` tool's real output is moved into `ToolReturn.metadata` and the
   model gets a fixed placeholder. pydantic-ai model adapters serialize only the
   return part's `content`, never `metadata`, so the output never reaches the LLM.
3. History (`make_policy_history_processor`): earlier outputs of tools that are
   *now* `user_only` or `disabled` are redacted from what the model sees, so a
   policy change applies to the whole conversation. The cached originals are
   never mutated, so the UI transcript still shows them.

A policy snapshot is taken once per chat request, so a save never changes
behavior halfway through a turn.
"""
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional

from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    ToolReturn,
    ToolReturnPart,
)
from pydantic_ai.toolsets import WrapperToolset

from backend.chat_tools.policy import ChatToolPolicy, ToolMode

HIDDEN_RESULT_METADATA_KEY = "cde_user_only_tool_result"

USER_ONLY_DESCRIPTION_PREFIX = (
    "[Output hidden from you] This tool's result is shown directly to the user. "
    "You will only receive a confirmation that it ran. "
)
USER_ONLY_PLACEHOLDER = (
    "The tool `{tool}` ran successfully. Its output was shown directly to the user and is "
    "intentionally hidden from you by the app administrator's policy. Do not guess, infer or "
    "fabricate its contents. Tell the user the result is displayed above."
)
DISABLED_REDACTION = (
    "The output of tool `{tool}` is no longer available: the app administrator has turned "
    "this tool off. Do not guess, infer or fabricate its contents."
)
USER_ONLY_ERROR_GENERIC = (
    "The tool failed (HTTP {status}). Details were shown to the user but are hidden from you "
    "because they may contain data. Check that column names and types in your arguments are correct."
)
USER_ONLY_ERROR_NO_STATUS = (
    "The tool failed. Details were shown to the user but are hidden from you "
    "because they may contain data. Check that column names and types in your arguments are correct."
)
TOOL_NOT_AVAILABLE = "Tool {tool!r} is not available."

_FASTAPI_MCP_ERROR = re.compile(r"Status code:\s*(\d+)\.\s*Response:\s*(.*)\Z", re.DOTALL)


# ----- model-safe errors (D8) -----

def model_safe_error_message(tool_name: str, error_text: str) -> str:
    """What a model may be told about a failed `user_only` tool call.

    `FastApiMCP` reports failures as `Error calling <tool>. Status code: N.
    Response: <JSON body>`. The body can embed data values, so only text the
    MCP server explicitly marked `model_safe_detail` (or a 422 validation error,
    which only echoes the model's own arguments) is passed through; everything
    else becomes a generic message. The user's card always gets the full text.
    """
    match = _FASTAPI_MCP_ERROR.search(error_text)
    if not match:
        return USER_ONLY_ERROR_NO_STATUS
    status = int(match.group(1))
    if status == 422:
        return error_text
    try:
        body = json.loads(match.group(2))
    except ValueError:
        body = None
    safe = body.get("model_safe_detail") if isinstance(body, dict) else None
    if isinstance(safe, str) and safe:
        return f"Error calling {tool_name}. Status code: {status}. {safe}"
    return USER_ONLY_ERROR_GENERIC.format(status=status)


# ----- size cap for stored hidden results -----

def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _cut_text(text: str, max_bytes: int) -> str:
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _longest_list(value: Any, path: tuple = ()) -> tuple:
    """(length, path) of the longest list reachable from `value` through dict keys."""
    if isinstance(value, list):
        return len(value), path
    best: tuple = (-1, None)
    if isinstance(value, dict):
        for key, child in value.items():
            candidate = _longest_list(child, path + (key,))
            if candidate[0] > best[0]:
                best = candidate
    return best


def _shrink_to_fit(content: Any, max_bytes: int) -> Any:
    """Keep the first N items of the largest list until the JSON fits, else cut the text."""
    current = json.loads(json.dumps(content, ensure_ascii=False, default=str))
    for _ in range(64):
        if _json_size(current) <= max_bytes:
            return current
        length, path = _longest_list(current)
        if path is None or length <= 0:
            break
        parent, node = None, current
        for step in path:
            parent, node = node, node[step]
        shrunk = node[: length // 2]
        if parent is None:
            current = shrunk
        else:
            parent[path[-1]] = shrunk
    text = json.dumps(current, ensure_ascii=False, default=str)
    return _cut_text(text, max_bytes)


def cap_hidden_result(content: Any, max_bytes: int) -> Dict[str, Any]:
    """Bound what is kept in chat history for one hidden result.

    Returns `{content, truncated, size_bytes}`; when over the cap, `content` is
    `{"preview": <first N items / cut text>, "truncated": True, "size_bytes": N}`.
    """
    size = _json_size(content)
    if size <= max_bytes:
        return {"content": content, "truncated": False, "size_bytes": size}
    budget = max(max_bytes - 200, 0)  # room for the wrapper keys
    preview = _shrink_to_fit(content, budget)
    return {
        "content": {"preview": preview, "truncated": True, "size_bytes": size},
        "truncated": True,
        "size_bytes": size,
    }


def tool_title(tool_name: str, description: Optional[str]) -> str:
    """`FastApiMCP` puts the route summary on the first line of the description."""
    for line in (description or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return tool_name


# ----- the toolset -----

@dataclass
class ChatToolPolicyToolset(WrapperToolset):
    """Wraps the MCP toolset and enforces one policy snapshot.

    Create a new instance per chat request: `hidden_errors` collects the full
    error text of failed `user_only` calls (shown to the user, never the model)
    and is read after the run.
    """

    policy: ChatToolPolicy = field(default_factory=ChatToolPolicy)
    max_result_bytes: int = 256 * 1024
    hidden_errors: List[Dict[str, Any]] = field(default_factory=list)

    async def get_tools(self, ctx):
        tools = await super().get_tools(ctx)
        visible = {}
        for name, tool in tools.items():
            mode = self.policy.mode_for(name)
            if mode is ToolMode.DISABLED:
                continue
            if mode is ToolMode.USER_ONLY:
                tool = replace(
                    tool,
                    tool_def=replace(
                        tool.tool_def,
                        description=USER_ONLY_DESCRIPTION_PREFIX + (tool.tool_def.description or ""),
                    ),
                )
            visible[name] = tool
        return visible

    async def call_tool(self, name, tool_args, ctx, tool):
        mode = self.policy.mode_for(name)
        if mode is ToolMode.DISABLED:  # defense in depth: the model is never offered it
            raise ModelRetry(TOOL_NOT_AVAILABLE.format(tool=name))
        if mode is not ToolMode.USER_ONLY:
            return await super().call_tool(name, tool_args, ctx, tool)

        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except ModelRetry as exc:
            self.hidden_errors.append({
                "tool_call_id": getattr(ctx, "tool_call_id", None),
                "tool_name": name,
                "title": tool_title(name, _original_description(tool)),
                "args": tool_args,
                "error": str(exc),
            })
            raise ModelRetry(model_safe_error_message(name, str(exc))) from None

        capped = cap_hidden_result(result, self.max_result_bytes)
        return ToolReturn(
            return_value=USER_ONLY_PLACEHOLDER.format(tool=name),
            metadata={HIDDEN_RESULT_METADATA_KEY: {
                "tool_name": name,
                "title": tool_title(name, _original_description(tool)),
                "args": tool_args,
                **capped,
            }},
        )


def _original_description(tool) -> Optional[str]:
    description = getattr(getattr(tool, "tool_def", None), "description", None) or ""
    return description.removeprefix(USER_ONLY_DESCRIPTION_PREFIX)


# ----- history -----

def make_policy_history_processor(policy: ChatToolPolicy):
    """History processor redacting earlier outputs of tools that are now restricted.

    Returns copies; the input messages (the cached chat history) are not mutated.
    Parts already carrying the placeholder (a `user_only` result from this or an
    earlier run) are left as they are: the model sees only `content`, and
    `metadata` holds the hidden result for the UI.
    """

    def redacted_content(part) -> Optional[str]:
        tool_name = part.tool_name
        if not tool_name:
            return None
        mode = policy.mode_for(tool_name)
        if mode is ToolMode.USER_ONLY:
            replacement = USER_ONLY_PLACEHOLDER.format(tool=tool_name)
        elif mode is ToolMode.DISABLED:
            replacement = DISABLED_REDACTION.format(tool=tool_name)
        else:
            return None
        # Already the placeholder: leave the part (and the hidden result in its
        # `metadata`, which the UI needs and adapters never serialize) untouched.
        return None if part.content == replacement else replacement

    def process(messages: List[ModelMessage]) -> List[ModelMessage]:
        result: List[ModelMessage] = []
        for message in messages:
            if not isinstance(message, ModelRequest):
                result.append(message)
                continue
            new_parts, changed = [], False
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    replacement = redacted_content(part)
                    if replacement is not None:
                        if isinstance(part, ToolReturnPart):
                            part = replace(part, content=replacement, metadata=None)
                        else:
                            part = replace(part, content=replacement)
                        changed = True
                new_parts.append(part)
            result.append(replace(message, parts=new_parts) if changed else message)
        return result

    return process


# ----- instructions -----

def build_policy_instructions(policy: ChatToolPolicy, visible_tool_names: Iterable[str]) -> Optional[str]:
    """Tell the model what is happening. Only added when a visible tool is `user_only`.

    This is *explanation*, not enforcement: the policy is enforced by the toolset.
    Uses `instructions` rather than `system_prompt` because pydantic-ai doesn't
    regenerate `system_prompt` when message history is passed, so a mid-conversation
    policy change would go stale.
    """
    hidden = sorted(name for name in visible_tool_names if policy.mode_for(name) is ToolMode.USER_ONLY)
    if not hidden:
        return None
    listing = "\n".join(f"- {name}" for name in hidden)
    return (
        "Some data tools are configured by the app administrator to show their output "
        "directly to the user and hide it from you:\n"
        f"{listing}\n"
        "When you call one of them, you only receive a confirmation that it ran; the user sees "
        "the result in the chat. Never guess, infer or fabricate its contents. Tell the user the "
        "result is displayed above and, where useful, suggest what to look at. You can still use "
        "your other tools to answer questions about the data."
    )
