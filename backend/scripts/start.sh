#!/usr/bin/env bash
set -euo pipefail

# Force unbuffered output so Docker logs capture everything in real time.
export PYTHONUNBUFFERED=1

# FastAPI on 8000. (MCP SSE is omitted in this build — for the demo we use
# the stdio transport configured in Claude Desktop. Re-enable later by
# adding `python -m src.mcp_server.server --transport sse --host 0.0.0.0
# --port 8001 &` and a `wait -n`.)
exec python -u -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1
