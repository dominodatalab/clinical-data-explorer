"""Small HTTP helpers for backend service calls."""

import requests


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

    if response.status_code in (401, 403):
        raise HTTPClientError(response.status_code, 'Access denied. Your session may have expired.')

    if response.status_code > 399:
        raise HTTPClientError(response.status_code, response.text)

    if is_json:
        return response.json()

    return response
