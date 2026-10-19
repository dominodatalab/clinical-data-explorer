"""Resolve the WebVFS endpoint available to the current Domino execution."""

from backend import config


def get_webvfs_base_url() -> str:
    """Return the configured WebVFS host without an API path.

    Current Domino releases provide DOMINO_WEBVFS_HOST. CDE's explicitly
    configured DOMINO_EXTERNAL_URL remains a legacy compatibility fallback
    where the public origin also serves the execution data plane's WebVFS
    routes. Request forwarding headers are never a safe source for a service
    endpoint.
    """
    webvfs_host = (config.get_domino_webvfs_host() or "").strip()
    external_url = (config.get_domino_external_url() or "").strip()
    host = webvfs_host or external_url
    if not host:
        raise RuntimeError(
            "Domino WebVFS host is not configured; set DOMINO_WEBVFS_HOST "
            "(or the legacy DOMINO_EXTERNAL_URL override)"
        )

    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"

    return host.rstrip('/')
