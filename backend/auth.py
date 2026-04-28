"""Auth helpers — passthrough token + Domino API base URL resolution.

When the app is accessed as a Domino Extension, the platform injects the
visiting user's JWT in the Authorization header of every request. We use it
to call Domino APIs on behalf of that user (datasets, governance, etc.).

`get_domino_api_host` resolves the in-cluster API host (used for Datasets,
Snapshots, Users, Projects APIs). `get_domino_external_url` resolves the
user-facing ingress host (required for /api/governance/v1/* which is only
exposed externally — see watch-out comment in `get_governance_api_url`,
which still lives in backend/app.py and will move to
backend/services/governance.py in step 1.4).

These helpers all run inside a Flask request context (they read
flask.request.headers); calling them outside one will raise.
"""
import logging
import os

from flask import request

logger = logging.getLogger(__name__)


def get_passthrough_token():
    """
    Extract the user's passthrough Bearer token from the request Authorization header.
    When the app is accessed as a Domino Extension, the platform injects the visiting
    user's JWT in the Authorization header of every request.
    """
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    return os.environ.get('DEV_ACCESS_TOKEN')


def get_domino_api_host():
    """
    Get the Domino API host URL.
    DOMINO_API_HOST_OVERD takes precedence over DOMINO_API_HOST when set.
    This allows overriding the auto-set DOMINO_API_HOST in Domino environments.
    """
    # Check override first
    domino_api_host = os.environ.get('DOMINO_API_HOST_OVERD')
    if domino_api_host:
        logger.debug(f"Using DOMINO_API_HOST_OVERD: {domino_api_host}")
        return domino_api_host.rstrip('/')

    # Fall back to standard env var
    domino_api_host = os.environ.get('DOMINO_API_HOST')
    if domino_api_host:
        return domino_api_host.rstrip('/')

    return None


def get_domino_external_url():
    """
    Get the externally-reachable Domino base URL (scheme + host).

    Unlike DOMINO_API_HOST (which points to the in-cluster nucleus-frontend service),
    this is the user-facing ingress URL. Some APIs — notably /api/governance/v1/* —
    are only registered at the external ingress and return 404 when hit via the
    internal service host.

    Resolution order:
      1. DOMINO_EXTERNAL_URL env var (manual override)
      2. X-Forwarded-Host / X-Forwarded-Proto request headers (set by the Domino
         reverse proxy when the app is deployed as a Domino App or Extension)
      3. VSCODE_PROXY_URI env var (set in Domino workspaces/IDE sessions)
    """
    override = os.environ.get('DOMINO_EXTERNAL_URL')
    if override:
        return override.rstrip('/')

    # Try request headers first — works for deployed Apps and Extensions
    try:
        from flask import has_request_context
        if has_request_context():
            fwd_host = request.headers.get('X-Forwarded-Host') or request.headers.get('Host')
            fwd_proto = request.headers.get('X-Forwarded-Proto', 'https')
            if fwd_host and 'domino-platform' not in fwd_host and 'localhost' not in fwd_host:
                # Use first host in a comma-separated list (proxies sometimes chain)
                fwd_host = fwd_host.split(',')[0].strip()
                return f"{fwd_proto}://{fwd_host}"
    except Exception as e:
        logger.debug(f"Could not derive external URL from request headers: {e}")

    # Workspace/IDE fallback
    proxy_uri = os.environ.get('VSCODE_PROXY_URI')
    if proxy_uri:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_uri)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception as e:
            logger.warning(f"Could not parse VSCODE_PROXY_URI: {e}")

    return None
