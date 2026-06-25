"""HTTP helpers. Just forwards arguments to the python requests library
with some overrides"""

from fastapi import HTTPException
import requests

from mcp_server.auth import get_domino_api_host, get_passthrough_token

class HTTPClientError(RuntimeError):
    """Raised when an HTTP helper call returns a non-success response."""

    def __init__(self, status_code: int, text: str):
        super().__init__(text)
        self.status_code = status_code
        self.text = text


def get(*args, is_json: bool = True, **kwargs):
    """Issue a GET request with backend defaults and uniform error handling."""
    response = requests.get(
        *args,
        **kwargs,
        timeout=120,
        stream=True
    )

    if response.status_code == 401:
        raise HTTPClientError(response.status_code, 'Authentication failed. Your session may have expired.')

    if response.status_code == 403:
        raise HTTPClientError(response.status_code, 'Access denied. You do not have permission to access this resource.')

    if response.status_code > 399:
        raise HTTPClientError(response.status_code, response.text)

    if is_json:
        return response.json()

    return response

def _pre_configured_get(path: str):
    domino_api_host = _get_domino_api_host()
    token = _get_passthrough_token()
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

    return get(
        f"{domino_api_host}{path}",
        headers=headers,
    )

def _get_domino_api_host() -> str:
    domino_api_host = get_domino_api_host()
    if not domino_api_host:
        raise HTTPException(
            status_code=503,
            detail="DOMINO_API_HOST not configured"
        )

def _get_passthrough_token() -> str:
    token = get_passthrough_token()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )

"""
API helpers
"""

def get_current_user():
    return _pre_configured_get('/api/users/v1/self')['user']
