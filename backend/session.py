"""Current-user session ID + MCP HTTP helpers.

The MCP server stores DataFrame state by logged-in Domino user ID. The
session ID is resolved from the current bearer token instead of a browser
cookie or an explicit forwarding header.

The helpers (`mcp_get`, `mcp_post`) are plain
callables that read `flask.session` / `flask.request` at call time, so
they only work inside a Flask request context.
"""
import contextvars
from flask import has_request_context
import requests

from backend.auth import get_passthrough_token
from backend import config
from backend.services.httpclient import get_current_user

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('current_user_id', default=None)

def initialize_session_id():
    """Resolve the logged-in Domino user ID for the current request."""
    user_id = get_current_user()['id']
    _current_user_id.set(user_id)
    return user_id


def get_session_id():
    """Return the logged-in Domino user ID."""
    user_id = _current_user_id.get()
    if not user_id:
        return initialize_session_id()

    return user_id


def _mcp_headers(session_id=None, headers=None):
    headers = dict(headers or {})
    if has_request_context():
        passthrough_token = get_passthrough_token()
        if passthrough_token:
            headers["Authorization"] = f"Bearer {passthrough_token}"
    return headers


def mcp_get(path, session_id=None, **kwargs):
    """GET request to MCP server with the current user's auth header."""
    headers = _mcp_headers(session_id=session_id, headers=kwargs.pop('headers', None))
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.get(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)


def mcp_post(path, session_id=None, **kwargs):
    """POST request to MCP server with the current user's auth header."""
    headers = _mcp_headers(session_id=session_id, headers=kwargs.pop('headers', None))
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.post(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)
