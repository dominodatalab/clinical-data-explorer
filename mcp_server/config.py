"""MCP server configuration constants."""

import logging
import os

LOG_LEVEL = os.environ.get('LOG_LEVEL', logging.INFO)


def get_domino_run_id():
    return os.environ.get("DOMINO_RUN_ID")

# Session lifecycle limits — used by mcp_server.session for eviction.
SESSION_MAX_AGE = int(os.environ.get('MCP_SESSION_MAX_AGE', 86400))  # evict sessions idle for more than 24 hours
DATAFRAME_MAX_AGE = int(os.environ.get('MCP_DATAFRAME_MAX_AGE', 900))  # evict cached dataframes idle for more than 15 minutes
DATASET_RELOAD_CONTEXT_MAX_AGE = int(os.environ.get('MCP_DATASET_RELOAD_CONTEXT_MAX_AGE', 86400))
SESSION_MAX_COUNT = int(os.environ.get('MCP_SESSION_MAX_COUNT', 50))  # hard cap on concurrent sessions

DEFAULT_DATAFRAME_CACHE_SIZE_BYTES = 1024 * 1024 * 1024
DATAFRAME_CACHE_SIZE_BYTES = int(os.environ.get('MCP_SERVER_DATAFRAME_CACHE_SIZE_B', DEFAULT_DATAFRAME_CACHE_SIZE_BYTES))
