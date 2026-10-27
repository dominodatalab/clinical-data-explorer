"""Auth helpers — passthrough token + Domino API base URL resolution.

When the app is accessed as a Domino Extension, the platform injects the
visiting user's JWT in the Authorization header of every request. We use it
to call Domino APIs on behalf of that user (datasets, governance, etc.).

`get_domino_api_host` resolves the in-cluster API host (used for Datasets,
Snapshots, Users, Projects APIs). `get_domino_external_url` returns the
deployment's public origin from DOMINO_URL.

Only helpers that read `flask.request` require a Flask request context.
"""
import logging
import os

from backend import config
from flask import request

logger = logging.getLogger(__name__)


def get_passthrough_token_from_authorization_header(auth_header):
    """Extract a passthrough bearer token from an Authorization header value."""
    if os.environ.get("DEV_MODE") == "dev":
      return config.get_dev_access_token()

    if auth_header and auth_header.startswith('Bearer '):
        return auth_header[7:]

    return config.get_dev_access_token()


def get_passthrough_token():
    """
    Extract the user's passthrough Bearer token from the request Authorization header.
    When the app is accessed as a Domino Extension, the platform injects the visiting
    user's JWT in the Authorization header of every request.
    """
    return get_passthrough_token_from_authorization_header(request.headers.get('Authorization', ''))


def get_domino_api_host():
    """
    Get the Domino API host URL.
    DOMINO_API_HOST_OVERD takes precedence over DOMINO_API_HOST when set.
    This allows overriding the auto-set DOMINO_API_HOST in Domino environments.
    """
    # Check override first
    domino_api_host = config.get_domino_api_host_override()
    if domino_api_host:
        logger.debug(f"Using DOMINO_API_HOST_OVERD: {domino_api_host}")
        return domino_api_host.rstrip('/')

    # Fall back to standard env var
    domino_api_host = config.get_domino_api_host()
    if domino_api_host:
        return domino_api_host.rstrip('/')

    return None


def get_domino_external_url():
    """Return the deployment's public origin configured in DOMINO_URL."""
    return config.get_domino_url()
