#!/bin/bash

# Data Explorer - Startup Script
# This script starts both the MCP server and Flask app

echo "=========================================="
echo "Starting Data Explorer Servers"
echo "=========================================="
echo ""

# Check if datasets folder exists
if [ ! -d "datasets" ]; then
    echo "⚠️  Warning: datasets folder not found"
    echo "Creating datasets folder..."
    mkdir datasets
fi

# Check for Python
if ! command -v python &> /dev/null; then
    echo "❌ Python not found. Please install Python 3.7 or higher."
    exit 1
fi

echo "✓ Python found: $(python --version)"
echo ""

# Check if requirements are installed
echo "Checking dependencies..."
python -c "import flask, fastapi, pydantic_ai, pandas" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  Some dependencies are missing."
    echo "Installing requirements..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "❌ Failed to install requirements. Please run: pip install -r requirements.txt"
        exit 1
    fi
fi
echo "✓ All dependencies installed"
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

# Verbose logging - uncomment the next line to enable DEBUG for all libraries (mcp, openai, etc.)
# export VERBOSE_LOGGING=true

# Start MCP Server
echo "Starting MCP Server on port 3333..."
python data_analysis_mcp.py > mcp_server.log 2>&1 &
MCP_PID=$!
echo "✓ MCP Server started (PID: $MCP_PID)"

# Wait a moment for MCP server to start
sleep 2

# Check if MCP server is running
if ! ps -p $MCP_PID > /dev/null; then
    echo "❌ MCP Server failed to start. Check mcp_server.log for details."
    exit 1
fi

# Start Flask App
FLASK_PORT=8888
echo "Starting Flask App on port $FLASK_PORT..."
python app.py $FLASK_PORT &
FLASK_PID=$!
echo "✓ Flask App started (PID: $FLASK_PID)"

# Wait a moment for Flask to start
sleep 2

# Check if Flask is running
if ! ps -p $FLASK_PID > /dev/null; then
    echo "❌ Flask App failed to start. Check flask_app.log for details."
    kill $MCP_PID 2>/dev/null
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
echo "MCP Server logs: mcp_server.log"
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

