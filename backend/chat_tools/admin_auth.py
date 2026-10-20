"""Who may open the chat tool settings page and change the policy.

The page route is not the security boundary: the app is visible to every
authenticated Domino user, so *every* admin endpoint goes through
`require_chat_tool_admin`, which resolves the caller's identity server-side with
their own token and fails closed.

Admin means editing rights on the extension's project (Owner or Contributor),
the same people who can edit the settings file in the Domino Dataset directly.
`CHAT_TOOL_ADMIN_USERNAMES` additionally grants admin, for local dev (no project)
and as a break-glass option.
"""
import logging
from functools import wraps
from typing import Optional
from urllib.parse import urlparse

import requests
from flask import g, jsonify, make_response, request

from backend import config
from backend.auth import get_domino_api_host, get_passthrough_token
from backend.services.httpclient import get_current_user

logger = logging.getLogger(__name__)

ADMIN_PROJECT_ROLES = frozenset({"owner", "contributor"})
FORBIDDEN_MESSAGE = "Chat tool settings are restricted to editors of the extension's project"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

FORBIDDEN_PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Access restricted</title>
<style>body{{font-family:-apple-system,"Segoe UI",sans-serif;margin:15vh auto;max-width:34rem;padding:0 1rem;color:#2e2e38}}</style>
</head><body><h1>Access restricted</h1><p>{FORBIDDEN_MESSAGE}.</p></body></html>
"""


def _normalize_role(role) -> str:
    return "".join(ch for ch in str(role or "").lower() if ch.isalnum())


def _current_user() -> dict:
    """The caller's Domino user, looked up once per request."""
    if not hasattr(g, "chat_tool_admin_user"):
        g.chat_tool_admin_user = get_current_user()
    return g.chat_tool_admin_user


def get_caller_project_role(project_id: str) -> Optional[str]:
    """The caller's role on `project_id`, looked up with the caller's own token.

    Returns e.g. "Owner" or "Contributor", or None when the caller has no role
    (or can't see the project at all). Everything Domino-API-specific about this
    check lives here, so the endpoint can be adjusted in one place.

    Uses the public project API (`GET /api/projects/v1/projects/{projectId}`, which needs
    `ListProject`). The response is `{"project": {"ownerId", "collaborators": [{"id", "role"}]}}`.
    Collaborator roles are lowercase (e.g. "contributor"); the project owner is `ownerId`, not a
    collaborator entry. A caller who can't see the project gets 401/403/404, treated as no role.

    NOTE: checked on a real Domino (2026-10-20, as the project owner, with an API key): the response
    shape above, `ownerId` equal to `id` from `/api/users/v1/self`, "contributor" collaborators, and a
    404 for an unknown project. Not re-checked on the public endpoint: a non-collaborator's token
    (403 on the previous internal endpoint) and the deployed App with the visiting user's JWT.
    """
    host = get_domino_api_host()
    token = get_passthrough_token()
    if not host or not token:
        raise RuntimeError("Domino API host or caller token is not available")

    response = requests.get(
        f"{host}/api/projects/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=15,
    )
    if response.status_code in (401, 403, 404):
        return None
    response.raise_for_status()

    project = response.json().get("project") or {}
    user_id = _current_user().get("id")
    if not user_id:
        return None
    if project.get("ownerId") == user_id:
        return "Owner"
    for collaborator in project.get("collaborators") or []:
        if collaborator.get("id") == user_id:
            return collaborator.get("role")
    return None


def resolve_admin() -> Optional[dict]:
    """Return `{id, username, via}` when the caller is a chat tool admin, else None. Fails closed."""
    try:
        user = _current_user()
    except Exception as exc:  # HTTPClientError, fastapi HTTPException, network errors
        logger.warning("Chat tool admin check denied: could not identify the caller (%s)", exc)
        return None

    username = user.get("userName") or ""
    identity = {"id": user.get("id"), "username": username or user.get("id")}

    if username.lower() in config.get_chat_tool_admin_usernames():
        return {**identity, "via": "allowlist"}

    project_id = config.get_domino_project_id()
    if not project_id:
        logger.warning("Chat tool admin check denied: DOMINO_PROJECT_ID is not set")
        return None
    try:
        role = get_caller_project_role(project_id)
    except Exception as exc:
        logger.warning("Chat tool admin check denied: project role lookup failed (%s)", exc)
        return None
    if _normalize_role(role) in ADMIN_PROJECT_ROLES:
        return {**identity, "via": "project-role"}
    logger.info("Chat tool admin check denied for %s (project role: %s)", identity["username"], role)
    return None


def _csrf_rejection() -> Optional[tuple]:
    """Reject cross-site state-changing calls.

    Domino's proxy attaches the JWT based on the browser's Domino session, so a
    page on another site could otherwise make a logged-in admin's browser send
    state-changing requests. Requiring JSON (which forces a CORS preflight) and a
    same-origin `Sec-Fetch-Site` / `Origin` closes that.
    """
    if request.method not in UNSAFE_METHODS:
        return None
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return jsonify({"error": FORBIDDEN_MESSAGE}), 403
    origin = request.headers.get("Origin")
    if origin:
        allowed_hosts = {request.host, request.headers.get("Host"), request.headers.get("X-Forwarded-Host")}
        if urlparse(origin).netloc not in {host for host in allowed_hosts if host}:
            return jsonify({"error": FORBIDDEN_MESSAGE}), 403
    return None


def require_chat_tool_admin(view=None, *, page: bool = False):
    """Decorator for admin routes. `page=True` answers non-admins with a static 403 page."""

    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            rejection = _csrf_rejection()
            if rejection is not None:
                return rejection
            admin = resolve_admin()
            if admin is None:
                if page:
                    response = make_response(FORBIDDEN_PAGE, 403)
                    response.headers["Content-Type"] = "text/html; charset=utf-8"
                    return response
                return jsonify({"error": FORBIDDEN_MESSAGE}), 403
            g.chat_tool_admin = admin
            return func(*args, **kwargs)

        return wrapper

    return decorate(view) if view is not None else decorate
