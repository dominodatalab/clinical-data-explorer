"""MCP server — FastAPI app factory, session middleware, MCP wiring.

Top-level structure of the MCP server. All 25 business endpoints live in
per-area `APIRouter` modules under `mcp_server/routes/`:

- `routes/analytics.py` — feature stats, correlations, group analysis
- `routes/charts.py`    — server-side chart aggregations
- `routes/datasets.py`  — dataset discovery + load + info + head + describe + data
- `routes/filters.py`   — expression filter + samples
- `routes/tables.py`    — paginated table data + column values + summary + column stats

Helpers live under `mcp_server/services/` (data_loading, columns, filters,
expressions); Pydantic request/response models live in `mcp_server/types.py`;
the per-session DataFrame storage + middleware lives in `mcp_server/session.py`.

`create_app()` is the canonical entry point and is now a real factory: it
constructs a fresh FastAPI instance, wires middleware, includes all five
per-area routers, attaches `FastApiMCP`, and mounts the MCP transport.
The module-level `app = create_app()` call below preserves the legacy
import path (`from mcp_server.app import app`) and the
`data_analysis_mcp.py` shim contract (`app`, `_sessions`,
`_convert_arrow_types`).

Per the §2 watch-out documented in REFACTOR_PROGRESS.md (P8), the MCP
mount() call snapshots `app.routes` at construction time, so all
`include_router(...)` calls MUST happen inside `create_app()` BEFORE
`FastApiMCP(app, ...)` is constructed. Routes added after that point
would still serve over HTTP but would NOT be exposed as MCP tools.

Reference for the original FastAPI-MCP wiring:
https://huggingface.co/blog/lynn-mikami/fastapi-mcp-server
"""
import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

from mcp_server.session import (
    SessionMiddleware,
    _current_session_id,
    _evict_stale_sessions,
    _get_session_dataset_name,
    _sessions,
    _set_current_df,
    get_current_df,
)
# `_convert_arrow_types` is re-imported from services.data_loading purely
# so the top-level `data_analysis_mcp.py` shim's `_convert_arrow_types`
# re-export (set up in step 2.1) keeps holding for any caller that imports
# the symbol from the legacy module path.
from mcp_server.services.data_loading import _convert_arrow_types
from mcp_server.routes.analytics import router as analytics_router
from mcp_server.routes.charts import router as charts_router
from mcp_server.routes.datasets import router as datasets_router
from mcp_server.routes.filters import router as filters_router
from mcp_server.routes.tables import router as tables_router

# Configure logging - write to stdout so logs appear in Domino app logs
logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Construct and fully wire a fresh MCP server FastAPI instance.

    Returns a new FastAPI app on every call. Wires session middleware,
    CORS, the welcome `/` route, all five per-area APIRouters, and
    finally the FastApiMCP transport. The MCP construction MUST come
    after `include_router(...)` calls because `FastApiMCP(app, ...)`
    snapshots `app.routes` at construction time and `mcp.mount()`
    snapshots them again — any route registered later would not be
    exposed as an MCP tool. See the P8 session log in
    REFACTOR_PROGRESS.md for the discovery.
    """
    app = FastAPI(
        title="Generic Dataset Analysis API",
        description="API for analyzing any CSV or Parquet dataset",
        version="1.0.0",
    )

    # Session middleware must be added before CORS
    app.add_middleware(SessionMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def read_root():
        return {"message": "Welcome to the Generic Dataset Analysis API"}

    app.include_router(analytics_router)
    app.include_router(charts_router)
    app.include_router(datasets_router)
    app.include_router(filters_router)
    app.include_router(tables_router)

    # Wire FastApiMCP after all routers so every business route is exposed
    # as an MCP tool (other than `load_dataset`, which is excluded).
    # Connect to this MCP by default with (in pydantic ai for example):
    #   server = MCPServerHTTP(url='http://localhost:3333/mcp')
    #   agent = Agent('openai:gpt-4.1-mini', mcp_servers=[server])
    # See chat_agent.py.
    mcp = FastApiMCP(
        app,
        name="Generic dataset analysis MCP server",
        description="MCP server for generic dataset analysis API - works with any CSV dataset",
        exclude_operations=["load_dataset"],
        # Forward session ID so MCP tool calls hit the right DataFrame
        headers=["authorization", "x-session-id"],
    )
    mcp.mount()

    return app


# Module-level singleton instance. Preserves the legacy import path
# (`from mcp_server.app import app`) and the `data_analysis_mcp.py` shim
# contract (which re-exports this symbol so existing callers in
# tests/conftest.py and tests/contract/test_mcp_parquet.py keep working).
app = create_app()
