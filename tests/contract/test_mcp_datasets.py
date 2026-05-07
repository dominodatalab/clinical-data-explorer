"""Contract tests: dataset load + info shape.

One test per feature (not per endpoint). We load in the fixture; here we just
assert that load succeeded with the expected metadata and that /dataset/info
reports the same facts back.
"""

from mcp_server.dataframe_cache import get_cache


def test_loads_csv_dataset(mcp_client):
    resp = mcp_client.get("/dataset/info")

    assert resp.status_code == 200
    body = resp.json()
    assert body["num_rows"] == 100
    # Fixture columns — fail loudly if the sample.csv schema drifts.
    assert set(body["columns"]) == {
        "subject_id", "age", "weight_kg", "treatment", "notes", "visit_date", "active_fl"
    }
    assert "age" in body["numeric_columns"]
    assert "treatment" in body["categorical_columns"]


def test_dataset_info_reloads_dataframe_after_cache_eviction(mcp_client):
    get_cache().clear()

    resp = mcp_client.get("/dataset/info")

    assert resp.status_code == 200
    body = resp.json()
    assert body["num_rows"] == 100
    assert "subject_id" in body["columns"]
