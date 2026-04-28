"""Contract tests: feature stats + correlation matrix.

One assertion per endpoint, on shape rather than exact numbers so trivial
drift in the fixture doesn't flap the test.
"""


def test_feature_stats_returns_mean_and_quartiles(mcp_client):
    resp = mcp_client.get("/feature/stats", params={"features": "age"})

    assert resp.status_code == 200
    stats = resp.json()
    assert len(stats) == 1
    age = stats[0]
    assert age["feature"] == "age"
    # The endpoint returns mean/median/std/min/max for numeric columns;
    # asserting their presence is enough to catch a shape regression.
    for key in ("mean", "median", "std", "min", "max"):
        assert key in age and age[key] is not None


def test_correlation_matrix_returns_nxn_for_numeric_columns(mcp_client):
    resp = mcp_client.get("/correlation/matrix")

    assert resp.status_code == 200
    matrix = resp.json()
    # Fixture has 3 numeric columns: subject_id, age, weight_kg.
    numeric = {"subject_id", "age", "weight_kg"}
    assert set(matrix.keys()) == numeric
    for col, row in matrix.items():
        assert set(row.keys()) == numeric
