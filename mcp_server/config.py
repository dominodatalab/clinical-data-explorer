"""MCP server configuration constants."""

import logging
import os
import sys

LOG_LEVEL = os.environ.get('LOG_LEVEL', logging.INFO)


def _get_worker_count() -> int:
    for env_name in ("MCP_WORKERS", "WEB_CONCURRENCY"):
        value = os.environ.get(env_name)
        if value:
            return int(value)

    for i, arg in enumerate(sys.argv):
        if arg == "--workers" and i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
        if arg.startswith("--workers="):
            return int(arg.split("=", 1)[1])

    return 1

# Session lifecycle limits — used by mcp_server.session for eviction.
SESSION_MAX_AGE = int(os.environ.get('MCP_SESSION_MAX_AGE', 900))  # evict sessions idle for more than 15 minutes
SESSION_MAX_COUNT = int(os.environ.get('MCP_SESSION_MAX_COUNT', 50))  # hard cap on concurrent sessions
MCP_CACHE_SERVER_URL = os.environ.get('MCP_CACHE_SERVER_URL')
MCP_WORKERS = _get_worker_count()

DEFAULT_DATAFRAME_CACHE_SIZE_BYTES = 1024 * 1024 * 1024
DATAFRAME_CACHE_SIZE_BYTES = int(os.environ.get('MCP_SERVER_DATAFRAME_CACHE_SIZE_B', DEFAULT_DATAFRAME_CACHE_SIZE_BYTES))
