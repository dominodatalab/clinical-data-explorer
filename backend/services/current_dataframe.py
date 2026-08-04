"""Refresh helpers for backend routes that require an active DataFrame."""
from functools import wraps
import logging

from flask import current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge, TooManyRequests

from backend.services.current_dataset_context import get_current_dataset_context
from backend.services.dataset_load_request_queue import DatasetLoadRequest
from backend.services.dataset_load_service import (
    DatasetLoadQueueFull,
    DatasetLoadTooLarge,
    get_current_session_dataframe_status,
    load_dataset_from_request_context,
)
from backend.session import get_session_id

logger = logging.getLogger(__name__)


def requires_current_dataframe(view):
    view.requires_current_dataframe = True

    @wraps(view)
    def wrapped(*args, **kwargs):
        return view(*args, **kwargs)

    wrapped.requires_current_dataframe = True
    return wrapped


def ensure_current_dataframe_loaded_for_request():
    view = current_app.view_functions.get(request.endpoint)
    if not getattr(view, "requires_current_dataframe", False):
        return None

    user_id = get_session_id()
    status = get_current_session_dataframe_status(user_id)
    if status.get("cache_hit"):
        return None

    context = get_current_dataset_context(user_id)
    if context is None:
        if status.get("loaded"):
            return None
        return jsonify({"error": "No dataset is currently loaded. Please load a dataset first."}), 400

    if context.is_local_filesystem_dataset():
        return None

    load_request = DatasetLoadRequest(
        dataset=context.dataset,
        session_id=user_id,
        authorization_header=request.headers.get("Authorization"),
        project_id=context.project_id,
        dataset_id=context.dataset_id,
        snapshot_id=context.snapshot_id,
        source_type=context.source_type,
        volume_key=context.volume_key,
        volume_id=context.volume_id,
        snapshot_version=context.snapshot_version,
    )

    try:
        result = load_dataset_from_request_context(
            load_request,
            clear_chat_history=False,
        )
    except DatasetLoadQueueFull as exc:
        raise TooManyRequests(
            description="Sorry, we can't process your dataset, this server is at capacity."
        ) from exc
    except DatasetLoadTooLarge as exc:
        raise RequestEntityTooLarge(description=exc.message) from exc

    if result.status_code >= 400:
        message = (
            result.payload.get("error")
            or result.payload.get("detail")
            or "This dataset file could not be refreshed because the source file no longer exists or is no longer accessible."
        )
        if result.status_code in (401, 403):
            message = "Your session no longer has access to this dataset. Refresh the page or load the dataset again."
        return jsonify({"error": message}), result.status_code

    logger.info("Refreshed DataFrame for user %s after MCP cache miss", user_id)
    return None
