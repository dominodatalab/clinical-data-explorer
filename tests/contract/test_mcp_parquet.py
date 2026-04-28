"""Contract tests: parquet loading + column-type classification.

Parquet columns arrive with PyArrow-backed dtypes (Int64 with nulls,
string[pyarrow], etc.) that pandas treats differently from the numpy-backed
dtypes you get from a CSV. data_analysis_mcp._convert_arrow_types is ~120
lines of normalization logic that runs on every parquet load — it has no
other coverage and is exactly the kind of code that breaks silently when
pandas/pyarrow versions move.

These tests load a small parquet built at runtime (no binary fixture in the
repo — the spec lives in this file so it's reviewable as plain text) and
assert the public dataset/info contract holds for the trickier dtypes:

  - nullable Int64 with NaN -> classified as numeric
  - float with NaN          -> classified as numeric
  - string                  -> classified as categorical

If the conversion regresses, /dataset/info will misclassify columns and
downstream filters/charts/stats will break in confusing ways. We catch it
here at the source instead.
"""
import sys
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def parquet_path(tmp_path):
    """Write a small parquet with deliberately tricky dtypes; return its path.

    Row count is small (10) because we're testing type handling, not data
    volume — bigger would slow tests without adding signal.
    """
    df = pd.DataFrame(
        {
            # Nullable Int64 with one NaN — pandas extension dtype that
            # _convert_arrow_types must demote to float64 (lines 220-225).
            "score": pd.array([10, 20, None, 40, 50, 60, 70, 80, 90, 100], dtype="Int64"),
            # Float with NaN — should round-trip as float64.
            "weight": [1.1, 2.2, np.nan, 4.4, 5.5, 6.6, 7.7, 8.8, 9.9, 10.1],
            # Plain string column — comes back string[pyarrow] from parquet.
            "group": ["A", "B", "A", "C", "B", "A", "C", "B", "A", "C"],
        }
    )
    path = tmp_path / "mixed_types.parquet"
    df.to_parquet(path, engine="pyarrow")
    return path


@pytest.fixture
def parquet_client(_mcp_app, parquet_path):
    """TestClient with the parquet fixture pre-loaded under a unique session."""
    session_id = f"pqtest-{uuid.uuid4().hex}"
    client = TestClient(_mcp_app, headers={"X-Session-Id": session_id})

    resp = client.post("/dataset/load", params={"dataset_name": str(parquet_path)})
    assert resp.status_code == 200, f"parquet load failed: {resp.status_code} {resp.text}"

    yield client

    from data_analysis_mcp import _sessions
    _sessions.pop(session_id, None)


def test_parquet_dataset_info_classifies_columns_correctly(parquet_client):
    body = parquet_client.get("/dataset/info").json()

    assert set(body["columns"]) == {"score", "weight", "group"}
    assert body["num_rows"] == 10

    # The load-bearing assertions: parquet-specific dtypes must survive
    # _convert_arrow_types and end up in the right buckets. If either of these
    # fails, the UI will show numeric columns in categorical pickers (or
    # vice-versa) and stats/charts will blow up.
    numeric = set(body["numeric_columns"])
    categorical = set(body["categorical_columns"])
    assert "score" in numeric, (
        f"nullable Int64 lost numeric classification after parquet load: "
        f"numeric_columns={numeric}"
    )
    assert "weight" in numeric, (
        f"float64 lost numeric classification after parquet load: "
        f"numeric_columns={numeric}"
    )
    assert "group" in categorical, (
        f"string column not classified as categorical after parquet load: "
        f"categorical_columns={categorical}"
    )


def test_parquet_numeric_filter_works_after_type_conversion(parquet_client):
    """Round-trip check: a filter on the converted Int64 column must actually run.

    Catches the case where _convert_arrow_types labels a column numeric but
    leaves it in a dtype that pandas comparisons can't handle.
    """
    resp = parquet_client.post(
        "/table/data",
        json={
            "page": 1,
            "page_size": 100,
            "filters": [{"column": "score", "operator": "gt", "value": "50"}],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["unfiltered_rows"] == 10
    assert 0 < body["total_rows"] < body["unfiltered_rows"]
    # Every returned row must satisfy score > 50. NaN rows are correctly
    # excluded by pandas comparison semantics; if any leak through, the
    # type conversion left the column in a state that breaks downstream math.
    for row in body["data"]:
        assert row["score"] is not None and row["score"] > 50, (
            f"row violates score > 50 filter: {row}"
        )
