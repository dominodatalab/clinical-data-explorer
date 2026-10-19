"""Resolve the WebVFS endpoint available to the current Domino execution."""

from backend import config


def get_webvfs_base_url() -> str:
    """Return the configured WebVFS host without an API path.

    Current Domino releases provide DOMINO_WEBVFS_HOST. Public origins and
    request forwarding headers are never safe sources for a service endpoint.
    """
    host = (config.get_domino_webvfs_host() or "").strip()
    if not host:
        raise RuntimeError("Domino WebVFS host is not configured; set DOMINO_WEBVFS_HOST")

    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"

    return host.rstrip('/')
