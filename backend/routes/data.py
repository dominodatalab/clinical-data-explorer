"""Data blueprint — singular `/dataset/*`, `/table/*`, `/column_labels`.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.5e — the
final and largest backend blueprint). Owns the nine endpoints that load
the active dataset, paginate it, and proxy filter/summary/expression
queries to the MCP server:

- `POST /dataset/load`
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
from werkzeug.exceptions import TooManyRequests

from backend.services.column_labels import load_column_labels
from backend.services.datasets import (
    process_dataset_load_request,
)
from backend.session import get_session_id, mcp_get, mcp_post

logger = logging.getLogger(__name__)

bp = Blueprint('data', __name__)


@bp.route('/dataset/load', methods=['POST'])
def load_dataset():
    """Load a specific dataset. In extension mode (projectId or datasetId in body), downloads via Domino API first."""
    request_json = request.get_json(silent=True) or {}
    dataset_name = request_json.get('dataset')
    project_id = request_json.get('projectId')
    dataset_id = request_json.get('datasetId')
    snapshot_id = request_json.get('snapshotId')
    source_type = request_json.get('sourceType')
    volume_key = request_json.get('volumeKey')
    snapshot_version = request_json.get('snapshotVersion')
    if not dataset_name:
        return jsonify({'error': 'No dataset name provided'}), 400

    try:
        # TODO this could wait for a while. can we have a multi minute timeout on requests?
        # should we have an expiration on requests?
        return dataset_load_request_queue.get_dataset_load_request_queue().submit_and_wait(
            dataset_load_request_queue.DatasetLoadRequest(
                dataset=dataset_name,
                session_id=get_session_id(),
                authorization_header=request.headers.get('Authorization'),
                project_id=project_id,
                dataset_id=dataset_id,
                snapshot_id=snapshot_id,
                source_type=source_type,
                volume_key=volume_key,
                snapshot_version=snapshot_version,
            ),
            process_dataset_load_request,
        )
    except dataset_load_request_queue.DatasetLoadRequestQueueFullError as exc:
        raise TooManyRequests(
            description="Sorry, we can't process your dataset, this server is at capacity."
        ) from exc

    except file_size_limits.DataFileTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail=exc.text,
        ) from exc


@bp.route('/dataset/data', methods=['GET'])
def get_dataset_data():
    """Get the current dataset data and metadata for visualization"""
    try:
        response = mcp_get("/dataset/data")
        if response.status_code == 200:
            return jsonify(response.json())
        elif response.status_code == 400:
            return jsonify({'error': 'No dataset loaded. Please load a dataset first.'}), 400
        else:
            error_detail = response.json().get('detail', 'Failed to get dataset data')
            return jsonify({'error': error_detail}), response.status_code
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
        response = mcp_post("/table/data", json=request.json)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': response.json().get('detail', 'Failed to get table data')}), response.status_code
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

        response = mcp_get(f"/table/column_values/{column}", params=params)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': response.json().get('detail', 'Failed to get column values')}), response.status_code
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
        response = mcp_post("/table/summary", json=request.json)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': response.json().get('detail', 'Failed to get summary')}), response.status_code
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

        response = mcp_get(f"/table/column_stats/{column}", params=params)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': response.json().get('detail', 'Failed to get column stats')}), response.status_code
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
        response = mcp_post("/table/expression_filter", json=request.json)
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            error_detail = response.json().get('detail', 'Failed to apply expression filter')
            return jsonify({'error': error_detail}), response.status_code
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
        response = mcp_get("/table/expression_samples")
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({'error': response.json().get('detail', 'Failed to get expression samples')}), response.status_code
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for expression samples")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting expression samples: {e}")
        return jsonify({'error': str(e)}), 500
