"""Contract tests for the MCP tool catalog the chat agent can see.

The chat tool policy (admin page) is keyed by MCP tool name. Those names are
FastAPI's auto-generated operation ids, so renaming a route function, path or
method renames the tool and silently resets its policy to the default. The
snapshot below makes that visible in review.
"""
import pytest

# (method, path) of every route that is plain HTTP for the Flask backend only.
INTERNAL_ROUTES = [
    ("get", "/"),
    ("get", "/datasets/list"),
    ("post", "/dataset/load"),
    ("post", "/dataframes/evict-stale"),
    ("get", "/dataframe/size"),
    ("get", "/dataframe/current-session"),
    ("post", "/dataframe/evict-current-session"),
]

EXPECTED_TOOL_NAMES = {
    "get_feature_stats_feature_stats_get",
    "get_correlation_matrix_correlation_matrix_get",
    "get_target_correlation_correlation_target_get",
    "get_feature_histogram_feature_histogram__feature__get",
    "get_feature_boxplot_feature_boxplot__feature__get",
    "get_feature_comparison_feature_comparison_get",
    "get_group_analysis_feature_group_analysis_get",
    "get_value_counts_feature_value_counts__feature__get",
    "get_bar_chart_data_chart_bar_aggregation_post",
    "get_xy_chart_data_chart_xy_data_post",
    "get_time_series_data_chart_time_series_post",
    "get_histogram_data_chart_histogram_post",
    "get_dataset_info_dataset_info_get",
    "get_dataset_metadata_dataset_metadata_get",
    "get_dataset_head_dataset_head_get",
    "get_dataset_description_dataset_describe_get",
    "get_dataset_data_dataset_data_get",
    "expression_filter_table_expression_filter_post",
    "get_expression_samples_table_expression_samples_get",
    "get_table_data_table_data_post",
    "get_column_values_table_column_values__column__get",
    "get_table_summary_table_summary_post",
    "get_column_stats_table_column_stats__column__get",
}


def _mcp(app):
    return app.state.mcp


def test_tool_catalog_snapshot(_mcp_app):
    """A renamed tool resets that tool's chat policy to the default; acknowledge it here."""
    assert {tool.name for tool in _mcp(_mcp_app).tools} == EXPECTED_TOOL_NAMES


def test_internal_routes_are_not_mcp_tools(_mcp_app):
    exposed = {
        (details["method"].lower(), details["path"])
        for details in _mcp(_mcp_app).operation_map.values()
    }
    for route in INTERNAL_ROUTES:
        assert route not in exposed, f"{route} must not be an agent tool"


def test_internal_routes_are_tagged_internal(_mcp_app):
    tagged = {
        (method.lower(), path)
        for path, item in _mcp_app.openapi()["paths"].items()
        for method, operation in item.items()
        if "internal" in operation.get("tags", [])
    }
    assert tagged == set(INTERNAL_ROUTES)


def test_preview_header_is_forwarded_to_tool_calls(_mcp_app):
    assert "x-chat-tool-preview" in _mcp(_mcp_app)._forward_headers


# The routes stay registered over plain HTTP: the Flask backend calls them on
# every dataset load and from the load queue.

def test_root_still_answers(mcp_client):
    resp = mcp_client.get("/")
    assert resp.status_code == 200
    assert "message" in resp.json()


def test_datasets_list_still_answers(mcp_client):
    resp = mcp_client.get("/datasets/list")
    assert resp.status_code == 200
    assert set(resp.json()) == {"datasets", "current_dataset"}


def test_dataset_load_still_answers(mcp_client):
    # mcp_client already loaded the sample through this route in its fixture.
    resp = mcp_client.get("/dataframe/current-session")
    assert resp.status_code == 200
    assert resp.json()["dataset"].endswith("sample.csv")


def test_dataframe_size_still_answers(mcp_client):
    resp = mcp_client.get("/dataframe/size")
    assert resp.status_code == 200
    assert resp.json()["dataframe_size_bytes"] > 0


def test_evict_stale_still_answers(mcp_client):
    resp = mcp_client.post("/dataframes/evict-stale")
    assert resp.status_code == 200
    assert set(resp.json()) == {"evicted_sessions", "evicted_dataframes"}


def test_evict_current_session_still_answers(mcp_client):
    resp = mcp_client.post("/dataframe/evict-current-session")
    assert resp.status_code == 200
    assert resp.json()["evicted_sessions"] == 1
    assert mcp_client.get("/dataframe/current-session").json() == {"dataset": None}
