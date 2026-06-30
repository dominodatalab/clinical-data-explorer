"""Per-tab session ID + MCP HTTP helpers.

Each browser tab/user gets a unique session ID stored in a Flask cookie.
We forward that ID to the MCP server in the X-Session-Id header so that
each user's DataFrame state stays isolated (the MCP server keys its
in-memory `_sessions` dict by this header).

The helpers (`mcp_get`, `mcp_post`) are plain
callables that read `flask.session` / `flask.request` at call time, so
they only work inside a Flask request context.
"""
import uuid

import contextvars
from flask import has_request_context, session
import requests

from backend.auth import get_passthrough_token
from backend import config
from backend.services.httpclient import get_current_user

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('current_user_id', default=None)

def get_session_id():
    """Return the current browser session ID."""
    if has_request_context():
        user_id = session.get('sid')
        if not user_id:
            user_id = str(uuid.uuid4())
            session['sid'] = user_id
        _current_user_id.set(user_id)
        return user_id

    user_id = _current_user_id.get()
    if not user_id:
        user_id = get_current_user()['id']
        _current_user_id.set(user_id)

    return user_id


def _mcp_headers(session_id=None, headers=None):
    headers = dict(headers or {})
    headers["X-Session-Id"] = session_id or get_session_id()
    if has_request_context():
        passthrough_token = get_passthrough_token()
        if passthrough_token:
            headers["Authorization"] = f"Bearer {passthrough_token}"
    return headers


def mcp_get(path, session_id=None, **kwargs):
    """GET request to MCP server with session ID header."""
    headers = _mcp_headers(session_id=session_id, headers=kwargs.pop('headers', None))
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.get(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)


def mcp_post(path, session_id=None, **kwargs):
    """POST request to MCP server with session ID header."""
    headers = _mcp_headers(session_id=session_id, headers=kwargs.pop('headers', None))
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.post(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)
