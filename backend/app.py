"""Flask backend — app factory and static-asset routes.

After the §1 extraction is complete (REFACTOR_PLAN.md steps 1.1–1.6),
this module is just the `create_app()` factory plus the two static-asset
handlers that serve the SPA. Every business route now lives in a
`backend/routes/<area>.py` blueprint and is registered below.

Helper functions referenced by the static handlers are kept at module
scope. The before_request session hook (`ensure_session_id`) is also
wired here because it is process-wide, not blueprint-scoped.
"""
from flask import Flask, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
import uuid
from pathlib import Path
import logging
import sys

from backend import config
from backend.routes.charts import bp as charts_bp
from backend.routes.chat import bp as chat_bp
from backend.routes.data import bp as data_bp
from backend.routes.datasets import bp as datasets_bp
from backend.routes.governance import bp as governance_bp
from backend.session import ensure_session_id

logger = logging.getLogger(__name__)


# Where each formerly-inline route now lives:
#
#   /chat, /chat/status, /chat/clear              -> backend/routes/chat.py
#   /governance/*                                 -> backend/routes/governance.py
#   /chart/*                                      -> backend/routes/charts.py
#   /datasets, /snapshots/*, /snapshot/*/files,
#     /netapp-volume/*/files                      -> backend/routes/datasets.py
#   /dataset/load, /dataset/data, /table/*,
#     /column_labels                              -> backend/routes/data.py
#
# Session helpers (ensure_session_id, get_session_id, mcp_get, mcp_post)
# live in backend/session.py. Auth helpers live in backend/auth.py.
# Service-layer helpers (dataset discovery, governance URL builder,
# column-label loader) live under backend/services/.


# ===== APP FACTORY =====

_LOGGING_CONFIGURED = False


def _configure_logging():
    """Configure root logging once per process. Safe to call multiple times."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    logging.basicConfig(
        level=logging.DEBUG if config.get_verbose_logging() else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('flask_app.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger.setLevel(logging.DEBUG)
    _LOGGING_CONFIGURED = True


def create_app():
    """Build and return a fully-wired Flask app instance.

    Called from the top-level `app.py` shim and from any test that wants
    its own isolated app. All business routes are registered as
    blueprints; the only inline routes are the SPA static-asset handlers.
    """
    _configure_logging()

    # Root the Flask app at the repo root (one level up from backend/) so that
    # `send_from_directory('chat_ui', ...)` keeps resolving to <repo>/chat_ui/.
    # Flask joins relative directories against `app.root_path`; if we leave it
    # at the default (the package dir), it would look for `backend/chat_ui/`.
    repo_root = str(Path(__file__).resolve().parent.parent)
    app = Flask(__name__, root_path=repo_root)

    # Secret key for Flask sessions (used to sign session cookies).
    # Each app restart generates a new key — sessions reset, which is fine.
    app.secret_key = config.get_flask_secret_key() or uuid.uuid4().hex

    # Apply ProxyFix middleware to handle reverse proxy headers (nginx on Domino)
    # This ensures Flask correctly handles X-Forwarded-* headers from the proxy
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    _register_static_routes(app)
    app.register_blueprint(charts_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(datasets_bp)
    app.register_blueprint(governance_bp)
    return app


def _register_static_routes(app):
    """Wire the SPA static-asset handlers and the per-request session hook.

    Kept inline (rather than promoted to a blueprint) because they are
    factory-level concerns — the before_request hook is process-wide and
    the two `send_from_directory` handlers serve the SPA shell.
    """
    app.before_request(ensure_session_id)

    @app.route('/')
    def serve_index():
        return send_from_directory('chat_ui', 'index.html')

    @app.route('/<path:path>')
    def serve_static(path):
        return send_from_directory('chat_ui', path)
