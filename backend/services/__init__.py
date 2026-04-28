"""Backend service modules.

Service modules hold pure-ish helper logic that route handlers call into.
They may return Flask `Response` objects (via `jsonify`) for endpoints that
were already shaped that way pre-refactor — preserving exact behavior is
the contract for this refactor (see REFACTOR_PLAN.md ground rule #2).
"""
