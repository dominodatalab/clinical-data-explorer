"""MCP server services package — pure helpers extracted from `mcp_server/app.py`.

Modules here should avoid importing from `mcp_server/app.py` (no
Flask/FastAPI route registration, no module-side-effect state) so they
can be safely imported by route modules without circular-import risk.
Per REFACTOR_PLAN.md §2 step 2.4 the targets are:

- `data_loading.py` — file discovery, dataset loading, Arrow-type fixup
- `columns.py`      — robust numeric/categorical column detection
- `expressions.py`  — SAS/R/Python expression-filter translators
"""
