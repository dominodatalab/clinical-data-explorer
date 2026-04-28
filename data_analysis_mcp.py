"""Thin shim — MCP server entry point.

The real implementation now lives in `mcp_server/`. This file is kept so
existing invocations (`python data_analysis_mcp.py`, `start_servers.sh`)
and existing imports (`from data_analysis_mcp import app, _sessions` from
the contract tests in `tests/conftest.py` / `tests/contract/test_mcp_parquet.py`)
keep working unchanged.

See REFACTOR_PLAN.md §2 for the target layout.
"""
from mcp_server.app import app, _sessions, _convert_arrow_types  # re-exported for back-compat

__all__ = ["app", "_sessions", "_convert_arrow_types"]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3333)
