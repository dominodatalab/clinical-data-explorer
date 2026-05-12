#!/bin/bash

# Data Explorer - Production Startup Script
# Starts both the MCP server and Flask app using production servers.

set -u

cd "$(dirname "$0")"

echo "=========================================="
echo "Starting Data Explorer Servers"
echo "=========================================="
echo ""

# In production, this will refer to the pre-installed deps
prod_venv_dir="$HOME/clinical-data-explorer/.venv"
if [ -d "$prod_venv_dir" ]
then
    export UV_PROJECT_ENVIRONMENT="$prod_venv_dir"
else
    echo "prod venv directory doesn't exist"
fi

# Check if datasets folder exists
if [ ! -d "datasets" ]; then
    echo "Warning: datasets folder not found"
    echo "Creating datasets folder..."
    mkdir -p datasets
fi

MCP_HOST="${MCP_HOST:-0.0.0.0}"
MCP_PORT="${MCP_PORT:-3333}"
MCP_WORKERS="${MCP_WORKERS:-1}"

FLASK_HOST="${FLASK_HOST:-0.0.0.0}"
FLASK_PORT="${MAIN_APP_PORT:-8888}"
FLASK_WORKERS="${FLASK_WORKERS:-1}"
FLASK_THREADS="${FLASK_THREADS:-4}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"

UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"
GUNICORN_LOG_LEVEL="${GUNICORN_LOG_LEVEL:-info}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"

if [ -z "${MCP_SERVER_URL:-}" ]; then
    export MCP_SERVER_URL="http://127.0.0.1:${MCP_PORT}"
fi

MCP_PID=""
FLASK_PID=""

terminate_tree() {
    pid="$1"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        return
    fi

    child_pids="$(pgrep -P "$pid" 2>/dev/null)"
    kill "$pid" 2>/dev/null
    for child_pid in $child_pids; do
        terminate_tree "$child_pid"
    done
}

# Function to cleanup on exit
cleanup() {
    status="${1:-0}"
    trap - INT TERM EXIT
    echo ""
    echo "Shutting down servers..."
    terminate_tree "$MCP_PID"
    terminate_tree "$FLASK_PID"
    wait "$MCP_PID" 2>/dev/null
    wait "$FLASK_PID" 2>/dev/null
    echo "Servers stopped."
    exit "$status"
}

trap 'cleanup 0' INT TERM
trap 'cleanup $?' EXIT

# Verbose logging - uncomment the next line to enable DEBUG for all libraries (mcp, openai, etc.)
# export VERBOSE_LOGGING=true

date; echo "mcp start"
echo "Starting MCP Server on ${MCP_HOST}:${MCP_PORT}..."
uv run --locked uvicorn mcp_server.app:app \
    --host "$MCP_HOST" \
    --port "$MCP_PORT" \
    --workers "$MCP_WORKERS" \
    --proxy-headers \
    --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" \
    --log-level "$UVICORN_LOG_LEVEL" &
MCP_PID=$!
echo "MCP Server started (PID: $MCP_PID)"

# Wait a moment for MCP server to start
sleep 2

# Check if MCP server is running
if ! ps -p "$MCP_PID" > /dev/null; then
    echo "MCP Server failed to start."
    exit 1
fi

date; echo "Starting Flask App on port $FLASK_PORT..."
uv run --locked gunicorn app:app \
    --bind "${FLASK_HOST}:${FLASK_PORT}" \
    --workers "$FLASK_WORKERS" \
    --threads "$FLASK_THREADS" \
    --timeout "$GUNICORN_TIMEOUT" \
    --access-logfile - \
    --error-logfile - \
    --log-level "$GUNICORN_LOG_LEVEL" &
FLASK_PID=$!
echo "Flask App started (PID: $FLASK_PID)"

# Wait a moment for Flask to start
sleep 2

# Check if Flask is running
if ! ps -p "$FLASK_PID" > /dev/null; then
    echo "Flask App failed to start. Check flask_app.log for details."
    exit 1
fi

echo ""
echo "=========================================="
echo "Both servers are running!"
echo "=========================================="
echo ""
echo "MCP Server:     http://localhost:$MCP_PORT"
echo "Web Interface:  http://localhost:$FLASK_PORT"
echo ""
echo "MCP Server logs: console output below"
echo "Flask App logs: console output below"
echo ""
echo "Press Ctrl+C to stop both servers"
echo "=========================================="
echo ""

# Open browser (optional - uncomment if desired)
# sleep 1
# open http://localhost:$FLASK_PORT  # macOS
# xdg-open http://localhost:$FLASK_PORT  # Linux

while true; do
    if ! kill -0 "$MCP_PID" 2>/dev/null; then
        echo "MCP Server exited unexpectedly."
        cleanup 1
    fi
    if ! kill -0 "$FLASK_PID" 2>/dev/null; then
        echo "Flask App exited unexpectedly."
        cleanup 1
    fi
    sleep 2
done
