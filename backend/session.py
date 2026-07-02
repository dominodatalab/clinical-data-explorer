"""Current-user session ID + MCP HTTP helpers.

The current Domino user ID is the application session ID. Backend requests
forward the user's Authorization header to MCP; MCP resolves the same user ID
from that token and keys its in-memory session state with it.

The helpers (`mcp_get`, `mcp_post`) are plain
callables that read `flask.session` / `flask.request` at call time, so
they only work inside a Flask request context.
"""
import contextvars
import requests

from backend.auth import get_passthrough_token
from backend import config
from backend.services.httpclient import get_current_user

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar('current_user_id', default=None)


def refresh_current_user_id():
    """Resolve and cache the current Domino user ID for this request."""
    user_id = get_current_user()['id']
    _current_user_id.set(user_id)
    return user_id


def get_session_id():
    """Return the cached current Domino user's ID."""
    user_id = _current_user_id.get()

    if not user_id:
        user_id = refresh_current_user_id()

    return user_id


def mcp_get(path, session_id=None, **kwargs):
    """GET request to MCP server with the current user's auth header."""
    headers = dict(kwargs.pop('headers', None) or {})
    token = get_passthrough_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.get(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)


def mcp_post(path, session_id=None, **kwargs):
    """POST request to MCP server with the current user's auth header."""
    headers = dict(kwargs.pop('headers', None) or {})
    token = get_passthrough_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    kwargs.setdefault('timeout', config.MCP_REQUEST_TIMEOUT_SECONDS)
    return requests.post(f"{config.MCP_SERVER_URL}{path}", headers=headers, **kwargs)
