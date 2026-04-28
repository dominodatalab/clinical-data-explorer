"""Per-area FastAPI APIRouter modules for the MCP server.

Each module here defines a `router = APIRouter()` and decorates its route
handlers with `@router.{get,post,...}`; `mcp_server.app.create_app()`
wires them in via `app.include_router(router)`. This is the FastAPI
analog of Flask blueprints (see REFACTOR_PLAN.md S2, target layout).
"""
