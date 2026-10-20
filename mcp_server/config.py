"""MCP server configuration constants."""

import logging
import os

LOG_LEVEL = os.environ.get('LOG_LEVEL', logging.INFO)


def get_domino_run_id():
    return os.environ.get("DOMINO_RUN_ID")

# Session lifecycle limits — used by mcp_server.session for eviction.
SESSION_MAX_AGE = int(os.environ.get('MCP_SESSION_MAX_AGE', 900))  # evict sessions idle for more than 15 minutes
SESSION_MAX_COUNT = int(os.environ.get('MCP_SESSION_MAX_COUNT', 50))  # hard cap on concurrent sessions

DEFAULT_DATAFRAME_CACHE_SIZE_BYTES = 15032385536 # 14 GB
DATAFRAME_CACHE_SIZE_BYTES = int(os.environ.get('MCP_SERVER_DATAFRAME_CACHE_SIZE_B', DEFAULT_DATAFRAME_CACHE_SIZE_BYTES))

# Routes tagged with this are plain HTTP endpoints (used by the Flask backend for
# dataset loading and memory management) that must not be offered to the chat
# agent as MCP tools. See `FastApiMCP(exclude_tags=...)` in mcp_server/app.py.
INTERNAL_TAG = "internal"

# Header the Flask backend sets on the chat-tool admin page's preview calls.
# It routes them to a sandbox session so they never replace the owner's dataset.
PREVIEW_HEADER = "x-chat-tool-preview"
PREVIEW_SESSION_SUFFIX = "::chat-tool-preview"
