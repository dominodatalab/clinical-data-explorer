"""Thin shim — Flask entry point.

The real implementation now lives in `backend/`. This file is kept so existing
invocations (`python app.py [PORT]`, `start_servers.sh`) keep working.
"""
import logging
import os
import sys

from backend.app import create_app

app = create_app()

logging.basicConfig(level=os.environ.get('LOG_LEVEL', logging.INFO))

if __name__ == '__main__':
    # Ensure the chat_ui directory exists
    if not os.path.exists('chat_ui'):
        app.logger.error("Error: 'chat_ui' directory not found. Please ensure the frontend files are in a 'chat_ui' subdirectory.")
    else:
        # Get port from command line argument or use default
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888

        # Bind to 0.0.0.0 to allow connections from nginx reverse proxy (e.g., on Domino)
        # Can be overridden with FLASK_HOST environment variable
        host = os.environ.get('FLASK_HOST', '0.0.0.0')

        # Disable debug mode in production (when FLASK_DEBUG is not set or is 'false')
        debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'

        app.logger.info(f"Starting Flask app on {host}:{port} (debug={debug})")
        app.run(host=host, debug=debug, port=port)
