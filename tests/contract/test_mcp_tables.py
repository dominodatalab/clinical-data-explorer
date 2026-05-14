"""Contract tests: paginated table data + per-column stats.

Pagination is exercised via two fetches; the assertion is that the first row
differs between pages — covers pagination math and row-shape in one step.
"""
import json


def test_pagination_returns_different_rows_on_page_two(mcp_client):
    page1 = mcp_client.post("/table/data", json={"page": 1, "page_size": 10}).json()
    page2 = mcp_client.post("/table/data", json={"page": 2, "page_size": 10}).json()

    assert page1["total_rows"] == 100
    assert len(page1["data"]) == 10
    assert len(page2["data"]) == 10
    # First row of page 2 must not equal first row of page 1.
    assert page1["data"][0]["subject_id"] != page2["data"][0]["subject_id"]


def test_column_stats_returns_numeric_summary(mcp_client):
    resp = mcp_client.get("/table/column_stats/age")

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_numeric"] is True
    # Sanity-check the range rather than exact values — fixture seed could shift,
    # and we're testing the contract, not the specific numbers.
    assert body["min"] >= 18 and body["max"] <= 85
    assert "mean" in body


def test_table_summary_applies_simple_filters(mcp_client):
    resp = mcp_client.post(
        "/table/summary",
        json={"filters": [{"column": "treatment", "operator": "is", "value": "Placebo"}]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unfiltered_rows"] == 100
    assert body["total_rows"] == 20


def test_table_summary_applies_expression_filter(mcp_client):
    resp = mcp_client.post(
        "/table/summary",
        json={"expression": "age GE 50", "syntax": "sas"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unfiltered_rows"] == 100
    assert body["total_rows"] == 49


def test_column_stats_applies_simple_filters(mcp_client):
    filters = [{"column": "treatment", "operator": "is", "value": "Placebo"}]
    resp = mcp_client.get(
        "/table/column_stats/age",
        params={"filters": json.dumps(filters)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 20
    assert body["is_numeric"] is True


def test_column_stats_applies_expression_filter(mcp_client):
    resp = mcp_client.get(
        "/table/column_stats/weight_kg",
        params={"expression": "weight_kg IS NOT NULL", "syntax": "sas"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 93
    assert body["null_count"] == 0


def test_column_values_applies_simple_filters(mcp_client):
    filters = [{"column": "treatment", "operator": "is", "value": "Placebo"}]
    resp = mcp_client.get(
        "/table/column_values/treatment",
        params={"filters": json.dumps(filters)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["values"] == ["Placebo"]
    assert body["total_unique"] == 1


def test_column_values_applies_expression_filter(mcp_client):
    resp = mcp_client.get(
        "/table/column_values/weight_kg",
        params={"expression": "weight_kg IS NULL", "syntax": "sas"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["values"] == []
    assert body["total_unique"] == 0
