import contextvars
import os

_current_auth_header: contextvars.ContextVar[str] = contextvars.ContextVar('current_auth_header', default=None)

def get_dev_access_token() -> str:
    return os.environ.get("DEV_ACCESS_TOKEN")

def get_passthrough_token_from_authorization_header(auth_header):
    """Extract a passthrough bearer token from an Authorization header value."""
    if os.environ.get("DEV_MODE") == "dev":
      return get_dev_access_token()

    if auth_header and auth_header.startswith('Bearer '):
        return auth_header[7:]

    return get_dev_access_token()

def set_auth_header(headers):
    _current_auth_header.set(headers.get('Authorization'))

def get_passthrough_token():
    """
    Extract the user's passthrough Bearer token from the request Authorization header.
    When the app is accessed as a Domino Extension, the platform injects the visiting
    user's JWT in the Authorization header of every request.
    """
    return get_passthrough_token_from_authorization_header(_current_auth_header.get())

def get_domino_api_host():
    """
    Get the Domino API host URL.
    """
    return os.environ.get("DOMINO_API_HOST")
