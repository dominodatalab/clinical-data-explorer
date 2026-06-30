"""Charts blueprint — proxies the MCP server's chart aggregation endpoints.

Extracted from `backend/app.py` (REFACTOR_PLAN.md §1, step 1.5c). Owns
the four `/chart/*` aggregation endpoints (bar, xy, time series,
histogram). Behavior is preserved verbatim: same paths, same request /
response shapes, same status codes, same logging messages.

These handlers are thin proxies — every request body is forwarded to
the matching MCP endpoint, and the JSON response is returned unchanged.
The aggregations themselves run server-side on the MCP process so we
never transfer full datasets to the client.
"""
import logging

import requests
from flask import Blueprint, jsonify, request

import backend.services.dataframe_reload_helpers as dataframe_reload_helpers
from backend.services.mcp_proxy import proxied_mcp_json_response
from backend.session import get_session_id, mcp_post

logger = logging.getLogger(__name__)

bp = Blueprint('charts', __name__)


def _proxied_mcp_json_response(request_mcp_response, fallback_error):
    return proxied_mcp_json_response(
        request_mcp_response,
        fallback_error,
        lambda: dataframe_reload_helpers.try_reload_expired_dataframe(get_session_id()),
    )


@bp.route('/chart/bar_aggregation', methods=['POST'])
def get_bar_chart_data():
    """Get aggregated data for bar charts"""
    try:
        return _proxied_mcp_json_response(
            lambda: mcp_post("/chart/bar_aggregation", json=request.json),
            'Failed to get bar chart data',
        )
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for bar chart")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting bar chart data: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/chart/xy_data', methods=['POST'])
def get_xy_chart_data():
    """Get data for scatter/area charts with optional aggregation"""
    try:
        return _proxied_mcp_json_response(
            lambda: mcp_post("/chart/xy_data", json=request.json),
            'Failed to get XY chart data',
        )
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for XY chart")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting XY chart data: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/chart/time_series', methods=['POST'])
def get_time_series_data():
    """Get aggregated time series data"""
    try:
        return _proxied_mcp_json_response(
            lambda: mcp_post("/chart/time_series", json=request.json),
            'Failed to get time series data',
        )
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for time series")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting time series data: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/chart/histogram', methods=['POST'])
def get_histogram_data():
    """Get histogram data for a single column"""
    try:
        return _proxied_mcp_json_response(
            lambda: mcp_post("/chart/histogram", json=request.json),
            'Failed to get histogram data',
        )
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to MCP server for histogram")
        return jsonify({'error': 'Could not connect to MCP server'}), 503
    except Exception as e:
        logger.error(f"Error getting histogram data: {e}")
        return jsonify({'error': str(e)}), 500
