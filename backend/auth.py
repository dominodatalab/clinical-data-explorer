"""Auth helpers — passthrough token + Domino API base URL resolution.

When the app is accessed as a Domino Extension, the platform injects the
visiting user's JWT in the Authorization header of every request. We use it
to call Domino APIs on behalf of that user (datasets, governance, etc.).

`get_domino_api_host` resolves the in-cluster API host (used for Datasets,
Snapshots, Users, Projects APIs). `get_domino_external_url` attempts to
resolve a user-facing ingress host, but request headers cannot identify the
top-level Domino origin when App subdomains are enabled.

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
    """WARNING: DO NOT USE THIS AS A GENERAL-PURPOSE URL RESOLVER.

    In deployments with App subdomains enabled, X-Forwarded-Host and Host
    identify the App ingress rather than Domino's top-level public origin.
    The request-header fallback below therefore returns the wrong host in
    those deployments. API calls should use DOMINO_API_HOST or the relevant
    service-specific host when available. When DOMINO_WEBVFS_HOST is absent,
    WebVFS uses this resolver as a Domino 6.3.1 compatibility fallback.

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
    override = (config.get_domino_external_url() or "").strip()
    if override:
        return override.rstrip('/')

    # WARNING: THIS FALLBACK BREAKS WHEN APP SUBDOMAINS ARE ENABLED.
    # Forwarded request headers identify the App ingress in those deployments,
    # not the top-level Domino origin. Keep this behavior only for existing
    # callers that require compatibility with Domino versions lacking a
    # service-specific host.
    try:
        from flask import has_request_context
        if has_request_context():
            fwd_host = request.headers.get('X-Forwarded-Host') or request.headers.get('Host')
            fwd_proto = request.headers.get('X-Forwarded-Proto', 'https')
            if fwd_host and 'domino-platform' not in fwd_host and 'localhost' not in fwd_host:
                # Use first host in a comma-separated list (proxies sometimes chain)
                fwd_host = fwd_host.split(',')[0].strip()
                return f"{fwd_proto}://{fwd_host}".strip()
    except Exception as e:
        logger.debug(f"Could not derive external URL from request headers: {e}")

    # Workspace/IDE fallback
    proxy_uri = (config.get_vscode_proxy_uri() or "").strip()
    if proxy_uri:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_uri)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception as e:
            logger.warning(f"Could not parse VSCODE_PROXY_URI: {e}")

    return None
