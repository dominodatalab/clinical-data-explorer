"""Resolve the WebVFS endpoint available to the current Domino execution."""

import logging
from urllib.parse import urlsplit, urlunsplit

from flask import has_request_context, request

from backend import config

logger = logging.getLogger(__name__)


def _get_forwarded_request_origin() -> str:
    """Return the Domino 6.3.1 deployment origin from request headers."""
    try:
        if has_request_context():
            fwd_host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
            fwd_proto = request.headers.get("X-Forwarded-Proto", "https")
            if (
                fwd_host
                and "domino-platform" not in fwd_host
                and "localhost" not in fwd_host
            ):
                # Use first host in a comma-separated list (proxies sometimes chain)
                fwd_host = fwd_host.split(",")[0].strip()
                return f"{fwd_proto}://{fwd_host}"
    except Exception as e:
        logger.debug(f"Could not derive WebVFS origin from request headers: {e}")
    return ""


def get_webvfs_base_url() -> str:
    """Return the WebVFS origin for the current Domino version."""
    # Domino 6.4/7.0 and later inject the service-specific host. Prefer it so
    # App subdomains cannot redirect WebVFS traffic to the App ingress.
    host = (config.get_domino_webvfs_host() or "").strip()
    source = "DOMINO_WEBVFS_HOST"
    if not host:
        # Domino 6.3.1 does not inject DOMINO_WEBVFS_HOST, so retain its legacy
        # deployment-origin route derived from forwarded request headers.
        host = _get_forwarded_request_origin()
        source = "Domino WebVFS request origin"
    if not host:
        raise RuntimeError(
            "Domino WebVFS host is not configured and no forwarded request origin is available"
        )

    if not host.lower().startswith(("http://", "https://")):
        if "://" in host:
            raise RuntimeError(f"{source} must contain a valid HTTP(S) host")
        host = f"http://{host}"

    try:
        parsed = urlsplit(host)
        parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{source} must contain a valid HTTP(S) host") from exc

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError(f"{source} must contain a valid HTTP(S) host")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
