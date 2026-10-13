"""End-to-end: the admin page's preview path against a real MCP server.

Starts the MCP server on a local port and talks to it through the same MCP client
the chat agent uses (`MCPServerSSE`), so this covers the whole path: the preview
header is forwarded by FastApiMCP to the route call, `SessionMiddleware` puts the
call in the sandbox session, and the committed sample is shaped so that each kind
of tool parameter (numeric, categorical, date) has something to work with.
"""
import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from pydantic_ai.mcp import MCPServerSSE

from backend.chat_tools.preview_sample import SAMPLE_PATH

SAMPLE_CSV = str(SAMPLE_PATH)
OWN_CSV = "tests/fixtures/sample.csv"


@pytest.fixture(scope="module")
def live_mcp(_mcp_app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(_mcp_app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "MCP server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _mcp(base_url, preview):
    headers = {"Authorization": "Bearer test"}
    if preview:
        headers["X-Chat-Tool-Preview"] = "1"
    return MCPServerSSE(url=f"{base_url}/mcp", headers=headers)


def _call(base_url, name, args=None, preview=True):
    async def run():
        async with _mcp(base_url, preview) as server:
            return await server.direct_call_tool(name, args or {})

    return asyncio.run(run())


def _list(base_url):
    async def run():
        async with _mcp(base_url, True) as server:
            return await server.list_tools()

    return asyncio.run(run())


def _load(base_url, path, preview):
    headers = {"Authorization": "Bearer test"}
    if preview:
        headers["X-Chat-Tool-Preview"] = "1"
    response = httpx.post(f"{base_url}/dataset/load", params={"file_snapshot_path": path}, headers=headers, timeout=30)
    assert response.status_code == 200, response.text
    return response.json()


def _current(base_url, preview):
    headers = {"Authorization": "Bearer test"}
    if preview:
        headers["X-Chat-Tool-Preview"] = "1"
    return httpx.get(f"{base_url}/dataframe/current-session", headers=headers, timeout=30).json()["dataset"]


def test_list_tools_over_mcp_matches_the_catalog_and_hides_internal_routes(live_mcp):
    names = {tool.name for tool in _list(live_mcp)}

    assert len(names) == 23
    assert "get_dataset_info_dataset_info_get" in names
    assert not names & {"load_dataset", "evict_stale_dataframes", "evict_current_session_dataframe",
                        "get_current_dataframe_size", "get_current_session_dataframe"}
    assert not any(name.startswith(("read_root", "list_datasets")) for name in names)


def test_sample_loads_into_the_preview_session_with_every_column_type(live_mcp):
    loaded = _load(live_mcp, SAMPLE_CSV, preview=True)

    assert loaded["num_rows"] == 36
    assert {"AGE", "BMIBL", "CUMDOSE"} <= set(loaded["numeric_columns"])
    assert {"ARM", "SEX", "RACE"} <= set(loaded["categorical_columns"])
    assert {"TRTSDT", "TRTEDT"} <= set(loaded["columns"])  # ISO date strings for the date-typed parameters


def test_representative_tools_work_against_the_sample_through_mcp(live_mcp):
    _load(live_mcp, SAMPLE_CSV, preview=True)

    info = _call(live_mcp, "get_dataset_info_dataset_info_get")
    assert info["num_rows"] == 36 and "AGE" in info["columns"]

    group = _call(live_mcp, "get_group_analysis_feature_group_analysis_get", {"feature": "AGE", "group_by": "ARM"})
    assert "Placebo" in str(group) and "Xanomeline" in str(group)  # grouped statistics for the three arms

    series = _call(live_mcp, "get_time_series_data_chart_time_series_post",
                   {"date_column": "TRTSDT", "value_column": "AGE", "aggregation": "mean"})
    assert series["chart_type"] == "time_series"
    assert series["chart_data"] and {"x", "y"} <= set(series["chart_data"][0])

    histogram = _call(live_mcp, "get_histogram_data_chart_histogram_post", {"column": "AGE", "bins": 5})
    assert histogram["is_numeric"] is True and histogram["total_count"] == 36
    assert {"bin_start", "bin_end", "count"} <= set(histogram["chart_data"][0])

    bar = _call(live_mcp, "get_bar_chart_data_chart_bar_aggregation_post",
                {"category_column": "ARM", "aggregation": "count"})
    assert bar["total_categories"] == 3

    table = _call(live_mcp, "get_table_data_table_data_post", {"page": 1, "page_size": 5})
    assert len(table["data"]) == 5


@pytest.mark.xfail(
    reason="Pre-existing: /dataset/head returns a 500 when a returned row has a missing value (NaN is not "
           "JSON-serializable), so this tool can't preview the sample, which has blank DCSREAS values.",
    strict=False,
)
def test_dataset_head_on_data_with_missing_values(live_mcp):
    _load(live_mcp, SAMPLE_CSV, preview=True)

    head = _call(live_mcp, "get_dataset_head_dataset_head_get", {"rows": 3})

    assert len(head) == 3


def test_preview_calls_never_touch_the_real_session(live_mcp):
    _load(live_mcp, OWN_CSV, preview=False)

    _load(live_mcp, SAMPLE_CSV, preview=True)

    assert _current(live_mcp, preview=False).endswith("tests/fixtures/sample.csv")
    assert _current(live_mcp, preview=True).endswith("adsl_preview.csv")
    # Tool calls follow the same split: with the header they see the sample, without it the real dataset.
    assert "AGE" in _call(live_mcp, "get_dataset_info_dataset_info_get", preview=True)["columns"]
    real_columns = _call(live_mcp, "get_dataset_info_dataset_info_get", preview=False)["columns"]
    assert "subject_id" in real_columns and "AGE" not in real_columns


def test_preview_session_is_evicted_independently(live_mcp):
    _load(live_mcp, OWN_CSV, preview=False)
    _load(live_mcp, SAMPLE_CSV, preview=True)
    evicted = httpx.post(
        f"{live_mcp}/dataframe/evict-current-session",
        headers={"Authorization": "Bearer test", "X-Chat-Tool-Preview": "1"},
        timeout=30,
    ).json()

    assert evicted["evicted_sessions"] == 1
    assert _current(live_mcp, preview=True) is None
    assert _current(live_mcp, preview=False) is not None


def test_every_shipped_example_names_a_real_tool_and_runs_on_the_sample(live_mcp):
    from backend.chat_tools.catalog import PREVIEW_EXAMPLES_PATH
    import json

    _load(live_mcp, SAMPLE_CSV, preview=True)
    examples = json.loads(PREVIEW_EXAMPLES_PATH.read_text())
    tool_names = {tool.name for tool in _list(live_mcp)}

    assert set(examples) <= tool_names
    for name, args in examples.items():
        if name == "get_dataset_head_dataset_head_get":
            continue  # see the xfail above: a pre-existing NaN serialization bug
        result = _call(live_mcp, name, args)
        assert result is not None, name
