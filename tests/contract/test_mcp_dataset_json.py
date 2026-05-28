"""Contract tests: CDISC Dataset-JSON loading + column-type classification.

Dataset-JSON v1.1 has three wire encodings that all carry the same payload
(metadata header + positional row arrays):

  - .json   — one compact JSON object with `columns` and `rows`
  - .ndjson — newline-delimited: line 1 metadata, lines 2..N row arrays
  - .dsjc   — zLib/gzip-compressed NDJSON

`mcp_server.services.data_loading._load_dataset_json` reads each encoding,
then the shared `_convert_arrow_types` normalizer (covered by
test_mcp_parquet.py) classifies the columns. These tests load the real
fixtures in `datasets/testing_json/` (parity with the existing `ae.xpt`)
and assert the public /dataset/load contract holds across all three
encodings, plus the two fail-fast error paths for the generic `.json`
extension and corrupt `.dsjc`.
"""
import json
import sys
import uuid
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = REPO_ROOT / "datasets" / "testing_json"


@pytest.fixture
def json_client(_mcp_app):
    """TestClient under a unique session, no dataset pre-loaded."""
    session_id = f"dsjson-{uuid.uuid4().hex}"
    client = TestClient(_mcp_app, headers={"X-Session-Id": session_id})
    yield client
    from data_analysis_mcp import _sessions
    _sessions.pop(session_id, None)


@pytest.mark.parametrize("fixture_name", ["ae.json", "ae.ndjson", "ae.dsjc"])
def test_dataset_json_loads_and_classifies_columns(json_client, fixture_name):
    """All three encodings round-trip to the same 74x37 DataFrame and the
    columns land in the right type buckets."""
    resp = json_client.post(
        "/dataset/load",
        params={"file_snapshot_path": str(FIXTURES / fixture_name)},
    )
    assert resp.status_code == 200, f"{fixture_name} load failed: {resp.status_code} {resp.text}"
    body = resp.json()

    assert body["num_rows"] == 74
    assert len(body["columns"]) == 37

    assert "AESEQ" in body["numeric_columns"], (
        f"AESEQ should be numeric: numeric_columns={body['numeric_columns']}"
    )
    assert "AETERM" in body["categorical_columns"], (
        f"AETERM should be categorical: categorical_columns={body['categorical_columns']}"
    )
    assert "AESTDTC" in body["date_columns"], (
        f"AESTDTC should be detected as a date column: date_columns={body['date_columns']}"
    )


@pytest.mark.parametrize("fixture_name", ["ae.json", "ae.ndjson", "ae.dsjc"])
def test_dataset_json_numeric_filter_round_trips(json_client, fixture_name):
    """A numeric filter on the type-converted AESEQ column must actually run
    and return only matching rows — same guarantee as the parquet path."""
    load = json_client.post(
        "/dataset/load",
        params={"file_snapshot_path": str(FIXTURES / fixture_name)},
    )
    assert load.status_code == 200

    resp = json_client.post(
        "/table/data",
        json={
            "page": 1,
            "page_size": 100,
            "filters": [{"column": "AESEQ", "operator": "gt", "value": "5"}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unfiltered_rows"] == 74
    assert 0 < body["total_rows"] < body["unfiltered_rows"]
    for row in body["data"]:
        assert row["AESEQ"] is not None and row["AESEQ"] > 5, (
            f"row violates AESEQ > 5 filter: {row}"
        )


def test_non_dataset_json_rejected(json_client, tmp_path):
    """A .json file that parses but isn't Dataset-JSON shape fails fast with
    a 400 (it could be a package.json, a pandas to_json dump, anything)."""
    bogus = tmp_path / "package.json"
    bogus.write_text(json.dumps({"name": "my-pkg", "version": "1.0.0"}))

    resp = json_client.post("/dataset/load", params={"file_snapshot_path": str(bogus)})
    assert resp.status_code == 400, resp.text
    assert "not a CDISC Dataset-JSON file" in resp.json()["detail"]


def test_corrupt_dsjc_rejected(json_client, tmp_path):
    """A .dsjc that isn't a valid zLib/gzip stream surfaces a friendly 400
    instead of an opaque 'Error -3 while decompressing data'."""
    bad = tmp_path / "broken.dsjc"
    bad.write_bytes(b"this is not compressed data")

    resp = json_client.post("/dataset/load", params={"file_snapshot_path": str(bad)})
    assert resp.status_code == 400, resp.text
    assert "could not be decompressed as DSJC" in resp.json()["detail"]
