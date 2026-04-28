"""Per-tab session ID + MCP HTTP helpers.

Each browser tab/user gets a unique session ID stored in a Flask cookie.
We forward that ID to the MCP server in the X-Session-Id header so that
each user's DataFrame state stays isolated (the MCP server keys its
in-memory `_sessions` dict by this header).

`ensure_session_id` is a Flask before_request handler — it must be
registered against the app inside `create_app()` (see `backend/app.py`).
The other helpers (`get_session_id`, `mcp_get`, `mcp_post`) are plain
callables that read `flask.session` / `flask.request` at call time, so
they only work inside a Flask request context.
"""
import uuid

import requests
from flask import session

from backend import config


def ensure_session_id():
    """Flask `before_request` handler — assigns a session ID to every request."""
    if 'sid' not in session:
        session['sid'] = uuid.uuid4().hex
        session.permanent = True


def get_session_id():
    """Return the current session's ID, or 'default' if none is set yet."""
    return session.get('sid', 'default')


def mcp_get(path, **kwargs):
    """GET request to MCP server with session ID header."""
    headers = kwargs.pop('headers', {})
    headers['X-Session-Id'] = get_session_id()
    return requests.get(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)


def mcp_post(path, **kwargs):
    """POST request to MCP server with session ID header."""
    headers = kwargs.pop('headers', {})
    headers['X-Session-Id'] = get_session_id()
    return requests.post(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)
