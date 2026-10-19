"""Resolve the WebVFS endpoint available to the current Domino execution."""

from urllib.parse import urlsplit, urlunsplit

from backend import config


def get_webvfs_base_url() -> str:
    """Return the configured WebVFS host without an API path.

    Current Domino releases provide DOMINO_WEBVFS_HOST. Public origins and
    request forwarding headers are never safe sources for a service endpoint.
    """
    host = (config.get_domino_webvfs_host() or "").strip()
    if not host:
        raise RuntimeError("Domino WebVFS host is not configured; set DOMINO_WEBVFS_HOST")

    if not host.lower().startswith(("http://", "https://")):
        if "://" in host:
            raise RuntimeError("DOMINO_WEBVFS_HOST must contain a valid HTTP(S) host")
        host = f"http://{host}"

    parsed = urlsplit(host)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("DOMINO_WEBVFS_HOST must contain a valid HTTP(S) host")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
