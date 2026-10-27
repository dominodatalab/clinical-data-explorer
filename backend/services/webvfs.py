"""Resolve the WebVFS endpoint available to the current Domino execution."""

from urllib.parse import urlsplit, urlunsplit

from backend import config
from backend.auth import get_domino_external_url


def get_webvfs_base_url() -> str:
    """Return the WebVFS origin for the current Domino version."""
    # Domino 6.4/7.0 and later inject the service-specific host for the
    # execution's data plane.
    host = config.get_domino_webvfs_host()
    error_message_source = "DOMINO_WEBVFS_HOST"
    if not host:
        # This fallback supports Domino 6.3.1, which does not inject
        # DOMINO_WEBVFS_HOST. The deployment origin must serve /webvfs/.
        host = (get_domino_external_url() or "").strip()
        error_message_source = "DOMINO_URL"
    if not host:
        raise RuntimeError(
            "DOMINO_WEBVFS_HOST is not configured and no external URL is available; set DOMINO_URL"
        )

    if not host.lower().startswith(("http://", "https://")):
        if "://" in host:
            raise RuntimeError(f"{error_message_source} must contain a valid HTTP(S) host")
        host = f"http://{host}"

    try:
        parsed = urlsplit(host)
        parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{error_message_source} must contain a valid HTTP(S) host") from exc

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError(f"{error_message_source} must contain a valid HTTP(S) host")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
