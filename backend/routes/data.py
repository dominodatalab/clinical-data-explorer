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
from flask import Blueprint, jsonify, request
from werkzeug.exceptions import (
    ServiceUnavailable,
)

from backend.services.column_labels import load_column_labels
import backend.services.dataframe_reload as dataframe_reload
from backend.services.mcp_proxy import proxied_mcp_json_response
from backend.session import get_session_id, mcp_get, mcp_post

logger = logging.getLogger(__name__)

bp = Blueprint('data', __name__)


@bp.route('/dataset/load', methods=['POST'])
def load_dataset():
    """Load a specific dataset. In extension mode (projectId or datasetId in body), downloads via Domino API first."""
    request_json = request.get_json(silent=True) or {}
    return _load_dataset_from_request_json(request_json)


def _load_dataset_from_request_json(request_json):
    if not request_json.get('dataset'):
        return jsonify({'error': 'No dataset name provided'}), 400

    return dataframe_reload.load_dataset_from_request_json(
        request_json,
        session_id=get_session_id(),
        authorization_header=request.headers.get('Authorization'),
    )


def _proxied_mcp_json_response(request_mcp_response, fallback_error):
    return proxied_mcp_json_response(
        request_mcp_response,
        fallback_error,
        lambda: dataframe_reload.try_reload_expired_dataframe(get_session_id()),
    )


@bp.route('/dataset/metadata', methods=['GET'])
def get_dataset_metadata():
    """Proxy the current dataset's verbatim embedded metadata from the MCP server.

    Errors are surfaced by raising werkzeug HTTPExceptions; the app-level
    handler renders them in the standardized {code, name, description} envelope.
    Any unhandled Exception bubbles to the same handler as a 500.
    """
    try:
        return _proxied_mcp_json_response(
            lambda: mcp_get("/dataset/metadata"),
            'No dataset loaded. Please load a dataset first.',
        )
    except requests.exceptions.ConnectionError as exc:
        logger.error("Could not connect to MCP server for dataset metadata")
        raise ServiceUnavailable(description="Could not connect to MCP server") from exc


@bp.route('/dataset/data', methods=['GET'])
def get_dataset_data():
    """Get the current dataset data and metadata for visualization"""
    try:
        return _proxied_mcp_json_response(
            lambda: mcp_get("/dataset/data"),
            'No dataset loaded. Please load a dataset first.',
        )
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
        return _proxied_mcp_json_response(
            lambda: mcp_post("/table/data", json=request.json),
            'Failed to get table data',
        )
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

        return _proxied_mcp_json_response(
            lambda: mcp_get(f"/table/column_values/{column}", params=params),
            'Failed to get column values',
        )
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
        return _proxied_mcp_json_response(
            lambda: mcp_post("/table/summary", json=request.json),
            'Failed to get summary',
        )
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

        return _proxied_mcp_json_response(
            lambda: mcp_get(f"/table/column_stats/{column}", params=params),
            'Failed to get column stats',
        )
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
        return _proxied_mcp_json_response(
            lambda: mcp_post("/table/expression_filter", json=request.json),
            'Failed to apply expression filter',
        )
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
        return _proxied_mcp_json_response(
            lambda: mcp_get("/table/expression_samples"),
            'Failed to get expression samples',
        )
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for expression samples")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting expression samples: {e}")
        return jsonify({'error': str(e)}), 500
