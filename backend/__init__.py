"""Backend package — Flask app factory and modules.

The Flask app entry point lives in `backend.app:create_app`. The top-level
`/app.py` is a thin shim that calls into this package so existing
invocations (`python app.py [PORT]`, `start_servers.sh`) keep working.
"""
