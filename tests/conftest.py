"""Pytest fixtures shared across contract tests.

The MCP server stores DataFrames in per-session dicts keyed by the X-Session-Id
header. Each test gets a fresh UUID so tests can't bleed state into each other.
"""
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = REPO_ROOT / "tests" / "fixtures" / "sample.csv"

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def _mcp_app():
    """Import the FastAPI app once per test session."""
    from data_analysis_mcp import app
    return app


@pytest.fixture
def mcp_client(_mcp_app):
    """TestClient with a unique session ID header and sample.csv pre-loaded.

    Yields a ready-to-use client whose subsequent requests all target the
    same session, so /dataset/info and friends can see the loaded DataFrame.
    """
    session_id = f"test-{uuid.uuid4().hex}"
    client = TestClient(_mcp_app, headers={"X-Session-Id": session_id})

    # Load the sample dataset via the public API — no internal poking.
    resp = client.post("/dataset/load", params={"file_snapshot_path": str(SAMPLE_CSV)})
    assert resp.status_code == 200, f"fixture load failed: {resp.status_code} {resp.text}"

    yield client

    # Drop the session from the module-level dict so long test runs don't accrete state.
    from data_analysis_mcp import _sessions
    _sessions.pop(session_id, None)
