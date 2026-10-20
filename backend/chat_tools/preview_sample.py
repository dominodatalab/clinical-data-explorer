"""The built-in clinical sample the admin page previews tools against.

It is loaded into a *preview* session on the MCP server (the backend sends
`X-Chat-Tool-Preview: 1`), so previewing never replaces the dataset the admin has
open in the extension. The load skips the Domino Datasets API and the admission
queue on purpose: the file is a few KB.
"""
from pathlib import Path

from backend.session import mcp_post

SAMPLE_PATH = Path(__file__).resolve().parent / "sample_data" / "adsl_preview.csv"
SAMPLE_LABEL = "Sample: CDISC pilot ADSL (36 subjects), for previewing tool output only."
PREVIEW_HEADERS = {"X-Chat-Tool-Preview": "1"}


def ensure_preview_dataset_loaded() -> dict:
    """Load (or re-use) the sample in the caller's preview session.

    Returns the MCP server's `/dataset/load` response (columns, column types, row
    count). The MCP server returns the cached DataFrame when it's already loaded and
    reloads it lazily when the session was evicted, so this is safe to call before
    every preview.
    """
    response = mcp_post(
        "/dataset/load",
        params={"file_snapshot_path": str(SAMPLE_PATH)},
        headers=PREVIEW_HEADERS,
    )
    response.raise_for_status()
    return response.json()
