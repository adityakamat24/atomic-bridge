#!/usr/bin/env bash
set -euo pipefail

# FastAPI on 8000
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1 &
FASTAPI_PID=$!

# MCP SSE on 8001
python -m src.mcp_server.server --transport sse --host 0.0.0.0 --port 8001 &
MCP_PID=$!

# If either dies, take the other down so Fly.io restarts the whole container.
wait -n $FASTAPI_PID $MCP_PID
echo "One server exited; shutting down container"
kill -TERM $FASTAPI_PID $MCP_PID 2>/dev/null || true
exit 1
