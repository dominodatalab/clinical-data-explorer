"""Resolve the WebVFS endpoint available to the current Domino execution."""

from urllib.parse import urlsplit, urlunsplit

from flask import has_request_context, request
from werkzeug.http import parse_list_header

from backend import config


def _get_forwarded_request_origin() -> str:
    """Return only the host and protocol selected by the app's ProxyFix middleware."""
    if not has_request_context():
        return ""
    forwarded_hosts = parse_list_header(request.headers.get("X-Forwarded-Host", ""))
    forwarded_protocols = parse_list_header(request.headers.get("X-Forwarded-Proto", ""))
    if not forwarded_hosts or not forwarded_protocols:
        return ""
    if "werkzeug.proxy_fix.orig" not in request.environ:
        return ""
    if (
        request.host.casefold() != forwarded_hosts[-1].casefold()
        or request.scheme.casefold() != forwarded_protocols[-1].casefold()
    ):
        return ""
    return request.host_url


def get_webvfs_base_url() -> str:
    """Return the WebVFS origin for the current Domino version."""
    # Domino 6.4/7.0 and later inject the service-specific host. Prefer it so
    # App subdomains cannot redirect WebVFS traffic to the App ingress.
    host = (config.get_domino_webvfs_host() or "").strip()
    source = "DOMINO_WEBVFS_HOST"
    if not host:
        # Domino 6.3.1 does not inject DOMINO_WEBVFS_HOST, so retain its legacy
        # deployment-origin route. The app's ProxyFix middleware resolves the
        # trusted forwarded scheme and host into request.host_url.
        host = _get_forwarded_request_origin()
        source = "Domino WebVFS request origin"
    if not host:
        raise RuntimeError(
            "Domino WebVFS host is not configured and no trusted forwarded request origin is available"
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
