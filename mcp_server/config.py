"""MCP server configuration constants.

Extracted from `mcp_server/app.py` as step 2.3 of REFACTOR_PLAN.md §2
(mirror of `backend/config.py` on the Flask side).

Currently only holds the session lifecycle limits. As more constants come
out of `app.py` in subsequent steps (e.g. dataset folder paths, supported
file extensions when those move into `services/data_loading.py` in step
2.4a) some of them may land here too — see the plan's target layout.
"""

# Session lifecycle limits — used by mcp_server.session for eviction.
SESSION_MAX_AGE = os.environ.get('MCP_SESSION_MAX_AGE', 3600)  # evict sessions idle for more than 1 hour
SESSION_MAX_COUNT = os.environ.get('MCP_SESSION_MAX_COUNT', 50)  # hard cap on concurrent sessions
