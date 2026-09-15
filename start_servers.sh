#!/bin/bash

# Data Explorer - Startup Script
# This script starts both the MCP server and Flask app

echo "=========================================="
echo "Starting Data Explorer Servers"
echo "=========================================="
echo ""

# In production, this will refer to the pre-installed deps
prod_venv_dir="$HOME/clinical-data-explorer/.venv"
if [ -d $prod_venv_dir ]
then
    export UV_PROJECT_ENVIRONMENT=$prod_venv_dir
else
    echo "prod venv directory doesn't exist"
fi

# Check if datasets folder exists
# # TODO is this folder made in the right place in order to use a domino dataset?
# is it just a random folder on the file system?
if [ ! -d "datasets" ]; then
    echo "⚠️  Warning: datasets folder not found"
    echo "Creating datasets folder..."
    mkdir datasets
fi

# Materialize the project environment ONCE, up front, before anything is
# launched. Both servers below run with `--no-sync` so that neither of them
# syncs the environment itself: two concurrent `uv run` invocations racing to
# build the same .venv corrupt each other, and the loser dies before its
# python process ever starts. That race is invisible when the venv is already
# warm — e.g. in a workspace where you ran `uv sync`, or in the extension image
# where the Dockerfile pre-builds $prod_venv_dir — and shows up when this
# script runs against a cold checkout.
echo "Syncing project dependencies..."
if ! uv sync; then
    echo "❌ uv sync failed. Cannot start the servers."
    exit 1
fi
echo "✓ Dependencies ready"
echo ""

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "Shutting down servers..."
    kill $MCP_PID 2>/dev/null
    kill $FLASK_PID 2>/dev/null
    echo "Servers stopped."
    exit 0
}

trap cleanup INT TERM

# Wait until a server is actually accepting connections on its port. Checking
# `ps` on the launched pid is not enough: that pid is the `uv run` wrapper,
# which stays alive long before (and after) the server itself is up, so a
# server that never binds still looks healthy.
STARTUP_TIMEOUT_SECONDS=${STARTUP_TIMEOUT_SECONDS:-120}
wait_for_port() {
    name="$1"
    port="$2"
    pid="$3"
    deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))

    while [ "$SECONDS" -lt "$deadline" ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "❌ $name exited before it started listening on port $port."
            return 1
        fi
        if (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
            echo "✓ $name is listening on port $port"
            return 0
        fi
        sleep 1
    done

    echo "❌ $name did not start listening on port $port within ${STARTUP_TIMEOUT_SECONDS}s."
    return 1
}

# Verbose logging - uncomment the next line to enable DEBUG for all libraries (mcp, openai, etc.)
# export VERBOSE_LOGGING=true

# Start MCP Server
date; echo "mcp start"
echo "Starting MCP Server on port 3333..."
uv run --no-sync python data_analysis_mcp.py &
MCP_PID=$!
echo "✓ MCP Server started (PID: $MCP_PID)"

# Wait for the MCP server to accept connections
if ! wait_for_port "MCP Server" 3333 "$MCP_PID"; then
    kill $MCP_PID 2>/dev/null
    exit 1
fi

# Start Flask App
FLASK_PORT=${MAIN_APP_PORT:-8888}
date; echo "Starting Flask App on port $FLASK_PORT..."
uv run --no-sync python app.py "$FLASK_PORT" &
FLASK_PID=$!
echo "✓ Flask App started (PID: $FLASK_PID)"

# Wait for Flask to accept connections
if ! wait_for_port "Flask App" "$FLASK_PORT" "$FLASK_PID"; then
    kill $MCP_PID 2>/dev/null
    kill $FLASK_PID 2>/dev/null
    exit 1
fi

echo ""
echo "=========================================="
echo "✅ Both servers are running!"
echo "=========================================="
echo ""
echo "📊 MCP Server:  http://localhost:3333"
echo "🌐 Web Interface: http://localhost:$FLASK_PORT"
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

# Wait for user to interrupt
wait
