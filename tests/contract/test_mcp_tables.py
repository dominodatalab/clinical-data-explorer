"""Contract tests: paginated table data + per-column stats.

Pagination is exercised via two fetches; the assertion is that the first row
differs between pages — covers pagination math and row-shape in one step.
"""


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
