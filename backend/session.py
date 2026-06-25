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
from flask import session
import requests

from backend.auth import get_passthrough_token
from backend import config
from backend.services.httpclient import get_current_user

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('current_user_id', default=None)

def get_session_id():
    """Return the current user's ID"""
    user_id = _current_user_id.get()

    if not user_id:
        user_id = get_current_user()['id']
        _current_user_id.set(user_id)

    return user_id


def mcp_get(path, session_id=None, **kwargs):
    """GET request to MCP server with session ID header."""
    headers = dict(kwargs.pop('headers', None) or {})
    headers['Authorization'] = f'Bearer {get_passthrough_token()}'
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.get(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)


def mcp_post(path, session_id=None, **kwargs):
    """POST request to MCP server with session ID header."""
    headers = dict(kwargs.pop('headers', None) or {})
    headers['Authorization'] = f'Bearer {get_passthrough_token()}'
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.post(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)
