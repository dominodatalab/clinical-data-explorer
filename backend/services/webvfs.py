"""Resolve the WebVFS endpoint available to the current Domino execution."""

from backend import config


def get_webvfs_base_url() -> str:
    """Return a base URL whose next path segment is a WebVFS API route.

    Current Domino releases provide DOMINO_WEBVFS_HOST. Older releases can use
    an explicitly configured DOMINO_EXTERNAL_URL, but request forwarding
    headers are never a safe source for a service endpoint.
    """
    webvfs_host = (config.get_domino_webvfs_host() or "").strip()
    external_url = (config.get_domino_external_url() or "").strip()
    host = webvfs_host or external_url
    if not host:
        raise RuntimeError(
            "Domino WebVFS host is not configured; set DOMINO_WEBVFS_HOST "
            "or DOMINO_EXTERNAL_URL"
        )

    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"

    return f"{host.rstrip('/')}/webvfs"
