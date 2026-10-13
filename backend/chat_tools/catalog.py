"""The tool catalog and live previews, read from the MCP server at runtime.

Nothing about the tools is hard-coded: the list, descriptions and parameter schemas
come from the MCP `list_tools()` response, the same catalog the chat agent sees. A
new route under `mcp_server/routes/` appears here automatically.

Uses the same client class as the agent (`MCPServerSSE`). Calls carry
`X-Chat-Tool-Preview: 1`, so they run in the caller's preview session.
"""
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic_ai import ModelRetry
from pydantic_ai.mcp import MCPServerSSE

from backend import config
from backend.chat_tools.toolset import tool_title

_RESPONSES_SPLIT = re.compile(r"\n+###\s*Responses:", re.IGNORECASE)
_EXAMPLE_BLOCK = re.compile(r"\*\*Example Response:\*\*\s*```(?:json)?\s*(.*?)\s*```", re.DOTALL)


@dataclass
class CatalogTool:
    name: str
    title: str
    description: str
    input_schema: Dict[str, Any]
    declared_output_example: Any = None


def split_description(description: Optional[str]) -> tuple:
    """(description without the generated `### Responses:` block, declared output example).

    `FastApiMCP` appends a block derived from the route's `response_model` when
    there is one. The example is parsed JSON when it parses, else the raw text.
    """
    text = description or ""
    parts = _RESPONSES_SPLIT.split(text, maxsplit=1)
    body = parts[0].strip()
    example = None
    if len(parts) > 1:
        match = _EXAMPLE_BLOCK.search(parts[1])
        if match:
            raw = match.group(1)
            try:
                example = json.loads(raw)
            except ValueError:
                example = raw
    return body, example


def to_catalog_tool(mcp_tool) -> CatalogTool:
    body, example = split_description(mcp_tool.description)
    return CatalogTool(
        name=mcp_tool.name,
        title=tool_title(mcp_tool.name, body),
        description=body,
        input_schema=mcp_tool.inputSchema or {"type": "object", "properties": {}},
        declared_output_example=example,
    )


def _server(authorization_header: str) -> MCPServerSSE:
    return MCPServerSSE(
        url=config.MCP_SERVER_MCP_URL,
        headers={"Authorization": authorization_header, "X-Chat-Tool-Preview": "1"},
        read_timeout=config.MCP_REQUEST_TIMEOUT_SECONDS,
    )


async def list_catalog(authorization_header: str) -> List[CatalogTool]:
    async with _server(authorization_header) as server:
        return [to_catalog_tool(tool) for tool in await server.list_tools()]


def approx_tokens(text: str) -> int:
    """Rough token estimate (about 4 characters each); good enough to spot a token blow-up."""
    return len(text) // 4


async def call_tool(authorization_header: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Call one tool and report what the model *would* receive.

    A tool failure is returned as `error` (the admin is evaluating the tool and
    sees the full text), not raised.
    """
    started = time.monotonic()
    async with _server(authorization_header) as server:
        try:
            content = await server.direct_call_tool(name, args)
        except ModelRetry as exc:
            return {
                "content": None,
                "content_text": None,
                "size_bytes": 0,
                "approx_tokens": 0,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "error": str(exc),
            }
    elapsed_ms = round((time.monotonic() - started) * 1000)
    content_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    return {
        "content": content,
        "content_text": content_text,
        "size_bytes": len(content_text.encode("utf-8")),
        "approx_tokens": approx_tokens(content_text),
        "elapsed_ms": elapsed_ms,
    }
