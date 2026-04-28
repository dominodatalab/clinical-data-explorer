"""MCP server package — split out of the monolithic top-level data_analysis_mcp.py.

See REFACTOR_PLAN.md §2 for the target layout. As of step 2.1 everything still
lives in `mcp_server/app.py`; subsequent steps (2.2–2.5) will carve it into
`types.py`, `session.py`, `config.py`, `services/*`, and `routes/*`.
"""
