from flask import jsonify

NO_DATASET_LOADED_MESSAGE = "No dataset loaded."


def mcp_response_json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def mcp_error_text(response, fallback):
    detail = mcp_response_json(response).get("detail", fallback)
    if isinstance(detail, dict):
        return detail.get("error") or detail.get("message") or fallback
    return detail


def mcp_error_payload(response, fallback):
    return {"error": mcp_error_text(response, fallback)}


def is_no_dataset_loaded_response(response):
    if response.status_code != 400:
        return False
    return NO_DATASET_LOADED_MESSAGE in str(mcp_error_text(response, ""))


def result_status_code(result):
    if isinstance(result, tuple) and len(result) >= 2:
        return result[1]
    return getattr(result, "status_code", 200)


def result_json(result):
    if isinstance(result, tuple) and result:
        return result[0].get_json(silent=True) or {}
    if hasattr(result, "get_json"):
        return result.get_json(silent=True) or {}
    if hasattr(result, "json"):
        return result.json() or {}
    return {}


def mcp_request_with_expired_dataframe_reload(request_mcp_response, reload_expired_dataframe, logger=None):
    """Reload expired session data, then retry the user's original MCP request once."""
    response = request_mcp_response()
    if not is_no_dataset_loaded_response(response):
        return response

    if logger:
        logger.warning(
            "MCP request reported no loaded dataset before reload attempt: %s",
            mcp_error_text(response, NO_DATASET_LOADED_MESSAGE),
        )

    reloaded, error_response = reload_expired_dataframe()
    if not reloaded:
        return error_response

    return request_mcp_response()


def proxied_mcp_json_response(request_mcp_response, fallback_error, reload_expired_dataframe, logger=None):
    response = mcp_request_with_expired_dataframe_reload(
        request_mcp_response,
        reload_expired_dataframe,
        logger=logger,
    )
    if hasattr(response, "get_json"):
        return response
    if response.status_code == 200:
        return jsonify(mcp_response_json(response))
    return jsonify(mcp_error_payload(response, fallback_error)), response.status_code
