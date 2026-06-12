"""Resolve Domino app launches back to their project extension context."""

import logging
from urllib.parse import quote

import backend.services.httpclient as httpclient
from backend import config
from backend.auth import get_domino_api_host, get_domino_external_url, get_passthrough_token

logger = logging.getLogger(__name__)

APP_SEARCH_LIMIT = 50


def resolve_launch_context():
    run_id = config.get_domino_run_id()
    if not run_id:
        return {
            "redirectUrl": None,
            "available": False,
            "reason": "DOMINO_RUN_ID is not set",
        }

    token = get_passthrough_token()
    if not token:
        return {
            "redirectUrl": None,
            "available": False,
            "reason": "Authentication is required to resolve the app launch context",
        }

    api_host = get_domino_api_host()
    external_url = get_domino_external_url()
    if not api_host or not external_url:
        return {
            "redirectUrl": None,
            "available": False,
            "reason": "Domino API host or external URL is not configured",
        }

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        project = _get_launch_project(api_host, run_id, headers)
        project_id = project.get("id")
        if not project_id:
            return {
                "redirectUrl": None,
                "available": False,
                "reason": f"No project was found for run {run_id}",
            }

        extension = _get_project_sidebar_extension(api_host, project_id, headers)
        if not extension:
            return {
                "redirectUrl": None,
                "available": False,
                "projectId": project_id,
                "reason": "No enabled project sidebar extension was found for this app's project",
            }

        redirect_url = _build_extension_url(external_url, project, extension["id"], project_id)
        return {
            "redirectUrl": redirect_url,
            "available": True,
            "projectId": project_id,
            "extensionId": extension["id"],
            "ownerUsername": project.get("ownerUsername"),
            "projectName": project.get("name"),
        }
    except Exception as exc:
        logger.warning("Could not resolve Domino launch context: %s", exc)
        return {
            "redirectUrl": None,
            "available": False,
            "reason": str(exc),
        }


def _get_launch_project(api_host, run_id, headers):
    app = _get_app_for_instance(api_host, run_id, headers)
    if not app:
        return {}

    return app.get("project") or {}


def _get_app_for_instance(api_host, run_id, headers):
    offset = 0
    while True:
        response = _list_apps(api_host, headers, offset)
        for app in response.get("items", []):
            if _app_current_instance_id(app) == run_id:
                return app

        metadata = response.get("metadata") or {}
        limit = metadata.get("limit") or APP_SEARCH_LIMIT
        offset = (metadata.get("offset") or offset) + limit
        total_count = metadata.get("totalCount")
        if not response.get("items") or (total_count is not None and offset >= total_count):
            return None

    return None


def _list_apps(api_host, headers, offset):
    try:
        return httpclient.get(
            f"{api_host}/api/apps/v1/apps",
            params={
                "limit": APP_SEARCH_LIMIT,
                "offset": offset,
                "sortField": "lastViewed",
                "sortOrder": "desc",
            },
            headers=headers,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to list Domino apps at offset {offset}: {exc}") from exc


def _app_current_instance_id(app):
    current_version = app.get("currentVersion") or {}
    current_instance = current_version.get("currentInstance") or app.get("currentInstance") or {}
    return current_instance.get("id")


def _get_project_sidebar_extension(api_host, project_id, headers):
    try:
        response = httpclient.get(
            f"{api_host}/api/extensions/beta/extensions-ui",
            params={
                "mount_point_type": "projectSidebar",
                "project_id": project_id,
            },
            headers=headers,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch project sidebar extensions for project {project_id}: {exc}") from exc

    for extension in response.get("data", []):
        project_sidebar = (extension.get("uiMountPointTypeConfigs") or {}).get("projectSidebar") or {}
        if project_sidebar:
            return extension
    return None


def _build_extension_url(external_url, project, extension_id, project_id):
    owner_username = project.get("ownerUsername")
    project_name = project.get("name")
    if not owner_username or not project_name:
        raise RuntimeError("Project owner username or name is missing")

    owner_path = quote(owner_username, safe="")
    project_path = quote(project_name, safe="")
    return (
        f"{external_url.rstrip('/')}/u/{owner_path}/{project_path}/extension"
        f"?mountPointType=projectSidebar&extensionId={quote(extension_id, safe='')}"
        f"&projectId={quote(project_id, safe='')}"
    )
