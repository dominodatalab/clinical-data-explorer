import logging

import requests
import backend.services.dataset_load_request_queue as dataset_load_request_queue
import backend.services.file_size_limits as file_size_limits
from flask import jsonify
from werkzeug.exceptions import (
    HTTPException,
    RequestEntityTooLarge,
    TooManyRequests,
)

from backend.services.datasets import (
    load_existing_session_dataframe,
    process_dataset_load_request,
    resolve_dataset_load_target,
)
from backend.services.dataset_reload_context import context_from_load_body, get_reload_context
from backend.services.mcp_proxy import result_status_code
from backend.session import mcp_get, mcp_post

logger = logging.getLogger(__name__)

DATA_RELOAD_MISSING_CONTEXT_MESSAGE = "Your data expired and couldn't be reloaded. Please select the file again."
DATA_RELOAD_NO_SPACE_MESSAGE = "Your data expired and we couldn't reload it because there's not enough space"


def _result_error_text(result):
    response = result[0] if isinstance(result, tuple) and result else result
    if hasattr(response, "get_json"):
        return (response.get_json(silent=True) or {}).get("error", "Could not reload expired data")
    return response.json().get("error", "Could not reload expired data")


def _evict_stale_dataframes_before_load(session_id):
    try:
        response = mcp_post("/dataframes/evict-stale", session_id=session_id)
        if response.status_code != 200:
            logger.warning("MCP stale DataFrame eviction returned HTTP %s", response.status_code)
    except requests.exceptions.ConnectionError:
        logger.warning("Could not connect to MCP server to evict stale DataFrames before dataset load")
    except requests.exceptions.RequestException as exc:
        logger.warning("Could not evict stale DataFrames before dataset load: %s", exc)


def _get_current_session_dataset(session_id):
    try:
        response = mcp_get("/dataframe/current-session", session_id=session_id)
    except requests.exceptions.ConnectionError:
        logger.warning("Could not connect to MCP server to check current session DataFrame")
        return None
    except requests.exceptions.RequestException as exc:
        logger.warning("Could not check current session DataFrame: %s", exc)
        return None

    if response.status_code != 200:
        logger.warning("MCP current session DataFrame check returned HTTP %s", response.status_code)
        return None
    return response.json().get("dataset")


def load_dataset_from_request_json(request_json, session_id, authorization_header=None):
    dataset_name = request_json.get('dataset')
    project_id = request_json.get('projectId')
    dataset_id = request_json.get('datasetId')
    file_path = request_json.get('filePath')
    snapshot_id = request_json.get('snapshotId')
    source_type = request_json.get('sourceType')
    volume_key = request_json.get('volumeKey')
    volume_id = request_json.get('volumeId')
    snapshot_version = request_json.get('snapshotVersion')
    if not dataset_name:
        return jsonify({'error': 'No dataset name provided'}), 400

    reload_context = context_from_load_body(request_json)
    load_request = dataset_load_request_queue.DatasetLoadRequest(
        dataset=dataset_name,
        session_id=session_id,
        authorization_header=authorization_header,
        project_id=project_id,
        dataset_id=dataset_id,
        file_path=file_path,
        snapshot_id=snapshot_id,
        source_type=source_type,
        volume_key=volume_key,
        volume_id=volume_id,
        snapshot_version=snapshot_version,
        reload_context=reload_context.to_load_body() if reload_context else None,
    )

    try:
        target = resolve_dataset_load_target(load_request)
        if _get_current_session_dataset(session_id) == target.file_snapshot_path:
            return load_existing_session_dataframe(load_request, target)

        _evict_stale_dataframes_before_load(session_id)
        return dataset_load_request_queue.get_dataset_load_request_queue().submit_and_wait(
            load_request,
            process_dataset_load_request,
        )
    except dataset_load_request_queue.DatasetLoadRequestQueueFullError as exc:
        raise TooManyRequests(
            description="Sorry, we can't process your dataset, this server is at capacity."
        ) from exc

    except file_size_limits.DataFileTooLarge as exc:
        raise RequestEntityTooLarge(
            description=str(exc),
        ) from exc


def try_reload_expired_dataframe(session_id):
    context = get_reload_context(session_id)
    if context is None:
        return False, {"error": DATA_RELOAD_MISSING_CONTEXT_MESSAGE}, 400

    try:
        response = load_dataset_from_request_json(context.to_load_body(), session_id=session_id)
    except (RequestEntityTooLarge, TooManyRequests) as exc:
        return False, {"error": DATA_RELOAD_NO_SPACE_MESSAGE}, exc.code
    except HTTPException as exc:
        return False, {"error": exc.description}, exc.code

    status_code = result_status_code(response)
    if status_code >= 400:
        error_text = _result_error_text(response)
        return False, {"error": error_text}, status_code
    return True, None, None
