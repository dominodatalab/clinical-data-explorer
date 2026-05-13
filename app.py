"""Thin shim — Flask entry point.

The real implementation now lives in `backend/`. This file is kept so existing
invocations (`python app.py [PORT]`, `start_servers.sh`) keep working.
"""
import logging
import os
import sys

from backend import config
from backend.app import create_app

app = create_app()

logging.basicConfig(level=config.LOG_LEVEL)

if __name__ == '__main__':
    # Ensure the chat_ui directory exists
    if not os.path.exists('chat_ui'):
        app.logger.error("Error: 'chat_ui' directory not found. Please ensure the frontend files are in a 'chat_ui' subdirectory.")
    else:
        # Get port from command line argument or use default
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888

        # Bind to 0.0.0.0 to allow connections from nginx reverse proxy (e.g., on Domino)
        host = config.get_flask_host()

        debug = config.get_flask_debug()

        app.logger.info(f"Starting Flask app on {host}:{port} (debug={debug})")
        app.run(host=host, debug=debug, port=port)
