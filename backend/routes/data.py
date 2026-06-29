"""Data blueprint — singular `/dataset/*`, `/table/*`, `/column_labels`.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.5e — the
final and largest backend blueprint). Owns the nine endpoints that load
the active dataset, paginate it, and proxy filter/summary/expression
queries to the MCP server:

- `POST /dataset/load`
- internal pre-load call to MCP `POST /dataframes/evict-stale`
- `GET  /dataset/data`
- `POST /table/data`
- `GET  /table/column_values/<column>`
- `POST /table/summary`
- `GET  /table/column_stats/<column>`
- `GET  /column_labels`
- `POST /table/expression_filter`
- `GET  /table/expression_samples`

Behavior is preserved verbatim: same paths, same query-param handling,
same response envelopes, same status codes (including 503 for MCP
ConnectionError, 400 for "no dataset loaded"), same logging messages.

Note: dataset *discovery* / browsing (plural `/datasets`,
`/snapshots/*`, `/snapshot/*/files`, `/netapp-volume/*/files`) lives in
`backend/routes/datasets.py` (step 1.5d). The split between `datasets`
and `data` tracks the plan's target layout, not the URL pluralization.
"""
import logging

import requests
import backend.services.dataset_load_request_queue as dataset_load_request_queue
import backend.services.file_size_limits as file_size_limits
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import (
    HTTPException,
    RequestEntityTooLarge,
    ServiceUnavailable,
    TooManyRequests,
)

from backend.services.column_labels import load_column_labels
from backend.services.datasets import (
    load_existing_session_dataframe,
    process_dataset_load_request,
    resolve_dataset_load_target,
)
from backend.services.dataset_reload_context import get_reload_context, save_reload_context
from backend.session import get_session_id, mcp_get, mcp_post

logger = logging.getLogger(__name__)

bp = Blueprint('data', __name__)

NO_DATASET_LOADED_MESSAGE = "No dataset loaded."
DATA_RELOAD_MISSING_CONTEXT_MESSAGE = "Your data expired and couldn't be reloaded. Please select the file again."
DATA_RELOAD_NO_SPACE_MESSAGE = "your data expired and we couldn't reload it because there's not enough space"


def _mcp_response_json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def _mcp_error_text(response, fallback):
    detail = _mcp_response_json(response).get("detail", fallback)
    if isinstance(detail, dict):
        return detail.get("error") or detail.get("message") or fallback
    return detail


def _mcp_error_payload(response, fallback):
    return {"error": _mcp_error_text(response, fallback)}


def _is_no_dataset_loaded_response(response):
    if response.status_code != 400:
        return False
    return NO_DATASET_LOADED_MESSAGE in str(_mcp_error_text(response, ""))


def _result_status_code(result):
    if isinstance(result, tuple) and len(result) >= 2:
        return result[1]
    return getattr(result, "status_code", 200)


def _result_json(result):
    if isinstance(result, tuple) and result:
        return result[0].get_json(silent=True) or {}
    if hasattr(result, "get_json"):
        return result.get_json(silent=True) or {}
    if hasattr(result, "json"):
        return result.json() or {}
    return {}


def _reload_failure_response(status_code, error_text):
    if status_code in (413, 429) or "capacity" in error_text.lower() or "too large" in error_text.lower():
        return jsonify({"error": DATA_RELOAD_NO_SPACE_MESSAGE}), status_code
    return jsonify({"error": error_text}), status_code


def _evict_stale_dataframes_before_load():
    try:
        response = mcp_post("/dataframes/evict-stale")
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


@bp.route('/dataset/load', methods=['POST'])
def load_dataset():
    """Load a specific dataset. In extension mode (projectId or datasetId in body), downloads via Domino API first."""
    request_json = request.get_json(silent=True) or {}
    return _load_dataset_from_request_json(request_json)


def _load_dataset_from_request_json(request_json):
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

    session_id = get_session_id()
    load_request = dataset_load_request_queue.DatasetLoadRequest(
        dataset=dataset_name,
        session_id=session_id,
        authorization_header=request.headers.get('Authorization'),
        project_id=project_id,
        dataset_id=dataset_id,
        file_path=file_path,
        snapshot_id=snapshot_id,
        source_type=source_type,
        volume_key=volume_key,
        volume_id=volume_id,
        snapshot_version=snapshot_version,
    )

    try:
        target = resolve_dataset_load_target(load_request)
        if _get_current_session_dataset(session_id) == target.file_snapshot_path:
            response = load_existing_session_dataframe(load_request, target)
            if _result_status_code(response) < 400:
                save_reload_context(session_id, request_json)
            return response

        _evict_stale_dataframes_before_load()
        # TODO this could wait for a while. can we have a multi minute timeout on requests?
        # should we have an expiration on requests?
        response = dataset_load_request_queue.get_dataset_load_request_queue().submit_and_wait(
            load_request,
            process_dataset_load_request,
        )
        if _result_status_code(response) < 400:
            save_reload_context(session_id, request_json)
        return response
    except dataset_load_request_queue.DatasetLoadRequestQueueFullError as exc:
        raise TooManyRequests(
            description="Sorry, we can't process your dataset, this server is at capacity."
        ) from exc

    except file_size_limits.DataFileTooLarge as exc:
        raise RequestEntityTooLarge(
            description=str(exc),
        ) from exc


def _try_reload_expired_dataframe():
    context = get_reload_context(get_session_id())
    if context is None:
        return False, (jsonify({"error": DATA_RELOAD_MISSING_CONTEXT_MESSAGE}), 400)

    try:
        response = _load_dataset_from_request_json(context.to_load_body())
    except (RequestEntityTooLarge, TooManyRequests) as exc:
        return False, (jsonify({"error": DATA_RELOAD_NO_SPACE_MESSAGE}), exc.code)
    except HTTPException as exc:
        return False, (jsonify({"error": exc.description}), exc.code)

    status_code = _result_status_code(response)
    if status_code >= 400:
        error_text = _result_json(response).get("error", "Could not reload expired data")
        return False, _reload_failure_response(status_code, error_text)
    return True, None


def _mcp_request_with_expired_dataframe_reload(request_mcp_response):
    response = request_mcp_response()
    if not _is_no_dataset_loaded_response(response):
        return response, None

    reloaded, error_response = _try_reload_expired_dataframe()
    if not reloaded:
        return None, error_response

    return request_mcp_response(), None


@bp.route('/dataset/metadata', methods=['GET'])
def get_dataset_metadata():
    """Proxy the current dataset's verbatim embedded metadata from the MCP server.

    Errors are surfaced by raising werkzeug HTTPExceptions; the app-level
    handler renders them in the standardized {code, name, description} envelope.
    Any unhandled Exception bubbles to the same handler as a 500.
    """
    try:
        response, error_response = _mcp_request_with_expired_dataframe_reload(lambda: mcp_get("/dataset/metadata"))
        if error_response is not None:
            return error_response
    except requests.exceptions.ConnectionError as exc:
        logger.error("Could not connect to MCP server for dataset metadata")
        raise ServiceUnavailable(description="Could not connect to MCP server") from exc

    if response.status_code == 200:
        return jsonify(response.json())
    if response.status_code == 400:
        return jsonify(_mcp_error_payload(response, 'No dataset loaded. Please load a dataset first.')), 400
    return jsonify(_mcp_error_payload(response, 'Failed to get dataset metadata')), response.status_code


@bp.route('/dataset/data', methods=['GET'])
def get_dataset_data():
    """Get the current dataset data and metadata for visualization"""
    try:
        response, error_response = _mcp_request_with_expired_dataframe_reload(lambda: mcp_get("/dataset/data"))
        if error_response is not None:
            return error_response
        if response.status_code == 200:
            return jsonify(response.json())
        elif response.status_code == 400:
            return jsonify(_mcp_error_payload(response, 'No dataset loaded. Please load a dataset first.')), 400
        else:
            return jsonify(_mcp_error_payload(response, 'Failed to get dataset data')), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server")
        return jsonify({'error': 'Could not connect to MCP server. Make sure it is running on port 8888.'}), 503
    except Exception as e:
        logger.error(f"Error getting dataset data: {e}")
        return jsonify({'error': str(e)}), 500


# ===== TABLE VIEW ENDPOINTS =====

@bp.route('/table/data', methods=['POST'])
def get_table_data():
    """Get paginated table data with filtering and sorting"""
    try:
        response, error_response = _mcp_request_with_expired_dataframe_reload(lambda: mcp_post("/table/data", json=request.json))
        if error_response is not None:
            return error_response
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(_mcp_error_payload(response, 'Failed to get table data')), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for table data")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting table data: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/table/column_values/<column>', methods=['GET'])
def get_column_values(column):
    """Get distinct values for a column (autocomplete)"""
    try:
        # Forward all query parameters (search, limit, filters, expression, syntax)
        params = {}
        if request.args.get('search'):
            params['search'] = request.args.get('search')
        if request.args.get('limit'):
            params['limit'] = request.args.get('limit')
        if request.args.get('filters'):
            params['filters'] = request.args.get('filters')
        if request.args.get('expression'):
            params['expression'] = request.args.get('expression')
        if request.args.get('syntax'):
            params['syntax'] = request.args.get('syntax')

        response, error_response = _mcp_request_with_expired_dataframe_reload(
            lambda: mcp_get(f"/table/column_values/{column}", params=params)
        )
        if error_response is not None:
            return error_response
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(_mcp_error_payload(response, 'Failed to get column values')), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for column values")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting column values: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/table/summary', methods=['POST'])
def get_table_summary():
    """Get summary statistics for filtered data"""
    try:
        response, error_response = _mcp_request_with_expired_dataframe_reload(lambda: mcp_post("/table/summary", json=request.json))
        if error_response is not None:
            return error_response
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(_mcp_error_payload(response, 'Failed to get summary')), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for summary")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting summary: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/table/column_stats/<column>', methods=['GET'])
def get_column_stats(column):
    """Get statistics for a specific column"""
    try:
        # Forward all query parameters (filters, expression, syntax)
        params = {}
        if request.args.get('filters'):
            params['filters'] = request.args.get('filters')
        if request.args.get('expression'):
            params['expression'] = request.args.get('expression')
        if request.args.get('syntax'):
            params['syntax'] = request.args.get('syntax')

        response, error_response = _mcp_request_with_expired_dataframe_reload(
            lambda: mcp_get(f"/table/column_stats/{column}", params=params)
        )
        if error_response is not None:
            return error_response
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(_mcp_error_payload(response, 'Failed to get column stats')), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for column stats")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting column stats: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/column_labels', methods=['GET'])
def get_column_labels():
    """Get column label mappings from CSV lookup file if it exists"""
    try:
        labels = load_column_labels()
        if labels is None:
            return jsonify({'labels': {}, 'available': False})
        return jsonify({'labels': labels, 'available': True})
    except Exception as e:
        logger.error(f"Error loading column labels: {e}")
        return jsonify({'labels': {}, 'available': False, 'error': str(e)})


# ===== EXPRESSION FILTER ENDPOINTS =====
# Allow filtering using SAS WHERE, R dplyr, or Python pandas syntax

@bp.route('/table/expression_filter', methods=['POST'])
def expression_filter():
    """Filter table data using expression syntax (SAS, R, or Python)"""
    try:
        response, error_response = _mcp_request_with_expired_dataframe_reload(
            lambda: mcp_post("/table/expression_filter", json=request.json)
        )
        if error_response is not None:
            return error_response
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(_mcp_error_payload(response, 'Failed to apply expression filter')), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for expression filter")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error applying expression filter: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/table/expression_samples', methods=['GET'])
def get_expression_samples():
    """Get sample column data for generating expression examples"""
    try:
        response, error_response = _mcp_request_with_expired_dataframe_reload(lambda: mcp_get("/table/expression_samples"))
        if error_response is not None:
            return error_response
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(_mcp_error_payload(response, 'Failed to get expression samples')), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for expression samples")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting expression samples: {e}")
        return jsonify({'error': str(e)}), 500
