"""Auth helpers — passthrough token + Domino API base URL resolution.

When the app is accessed as a Domino Extension, the platform injects the
visiting user's JWT in the Authorization header of every request. We use it
to call Domino APIs on behalf of that user (datasets, governance, etc.).

`get_domino_api_host` resolves the in-cluster API host (used for Datasets,
Snapshots, Users, Projects APIs).

Only helpers that read `flask.request` require a Flask request context.
"""
import logging
import os
from urllib.parse import urlparse

from backend import config
from flask import has_request_context, request

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
    """Resolve the external Domino base URL with Domino 6.3.x compatibility.

    Use the first available source, trimming whitespace and trailing slashes:
      1. DOMINO_URL, supplied by Domino 6.4/7.0 and later.
      2. DOMINO_EXTERNAL_URL, an optional manually configured public URL.
      3. X-Forwarded-Host / X-Forwarded-Proto, or Host with HTTPS by default.
      4. The origin of VSCODE_PROXY_URI for workspace/IDE sessions.
    Return None if no source is available. Only the header fallback requires
    a Flask request context.

    Domino 6.3.x (before 6.4/7.0) does not supply DOMINO_URL, so the other
    sources support those deployments. WARNING: with App subdomains enabled
    and neither DOMINO_URL nor DOMINO_EXTERNAL_URL configured, request
    headers identify the App ingress rather than the deployment origin.
    That can send browser redirects and API requests to the wrong host,
    resulting in missing routes/404s. Set DOMINO_EXTERNAL_URL explicitly in
    those deployments.

    This URL serves browser redirects and ingress-only APIs such as
    Governance. Prefer DOMINO_API_HOST or a service-specific host for APIs
    that support them; WebVFS prefers DOMINO_WEBVFS_HOST when supplied.
    """
    for configured_url in (config.get_domino_url(), config.get_domino_external_url()):
        url = (configured_url or "").strip()
        if url:
            # Preserve the scheme delimiter so malformed origins remain invalid.
            scheme, separator, remainder = url.partition('://')
            if separator:
                return f"{scheme}://{remainder.rstrip('/')}"
            return url.rstrip('/')

    if has_request_context():
        fwd_host = request.headers.get('X-Forwarded-Host') or request.headers.get('Host')
        fwd_proto = request.headers.get('X-Forwarded-Proto', 'https')
        if fwd_host and 'domino-platform' not in fwd_host and 'localhost' not in fwd_host:
            fwd_host = fwd_host.split(',')[0].strip().rstrip('/')
            return f"{fwd_proto}://{fwd_host}".strip()

    proxy_uri = (config.get_vscode_proxy_uri() or "").strip()
    if proxy_uri:
        try:
            parsed = urlparse(proxy_uri)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".rstrip('/')
        except ValueError:
            logger.warning("Could not parse VSCODE_PROXY_URI")

    return None
