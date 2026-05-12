"""MCP server configuration constants."""

import logging
import os

LOG_LEVEL = os.environ.get('LOG_LEVEL', logging.INFO)

# Session lifecycle limits — used by mcp_server.session for eviction.
SESSION_MAX_AGE = int(os.environ.get('MCP_SESSION_MAX_AGE', 900))  # evict sessions idle for more than 15 minutes
SESSION_MAX_COUNT = int(os.environ.get('MCP_SESSION_MAX_COUNT', 50))  # hard cap on concurrent sessions

DEFAULT_DATAFRAME_CACHE_SIZE_BYTES = 1024 * 1024 * 1024
DATAFRAME_CACHE_SIZE_BYTES = int(os.environ.get('MCP_SERVER_DATAFRAME_CACHE_SIZE_B', DEFAULT_DATAFRAME_CACHE_SIZE_BYTES))
