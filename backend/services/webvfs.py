"""Resolve the WebVFS endpoint available to the current Domino execution."""

from urllib.parse import urlsplit, urlunsplit

from backend import config


def get_webvfs_base_url() -> str:
    """Return the configured WebVFS origin without an API path."""
    host = (config.get_domino_webvfs_host() or "").strip()
    if not host:
        raise RuntimeError("Domino WebVFS host is not configured; set DOMINO_WEBVFS_HOST")

    if not host.lower().startswith(("http://", "https://")):
        if "://" in host:
            raise RuntimeError("DOMINO_WEBVFS_HOST must contain a valid HTTP(S) host")
        host = f"http://{host}"

    try:
        parsed = urlsplit(host)
        parsed.port
    except ValueError as exc:
        raise RuntimeError("DOMINO_WEBVFS_HOST must contain a valid HTTP(S) host") from exc

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError("DOMINO_WEBVFS_HOST must contain a valid HTTP(S) host")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
