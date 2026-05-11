"""MCP server entry point.

Exposes 6 tools (see `tools.py`) over either stdio (Claude Desktop) or
SSE (HTTP-based clients). Both transports run the same tool functions.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.api.deps import AppContainer, build_container
from src.config import get_settings
from src.mcp_server import tools

logger = logging.getLogger(__name__)


def build_server(container: AppContainer | None = None) -> FastMCP:
    """Construct the MCP server, wiring our 6 tools to the AppContainer.
    Tests pass a pre-built container; production builds one from Settings.
    """
    if container is None:
        container = build_container(get_settings())

    server = FastMCP("itsm-bridge")

    @server.tool(
        description=(
            "Natural-language interface to the ITSM mediation layer. "
            "Use for any question about IT tickets, users, teams, or knowledge "
            "articles. For state-changing requests, returns a write proposal "
            "that must be confirmed via confirm_write."
        )
    )
    async def query_itsm(question: str, session_id: str | None = None) -> dict[str, Any]:
        return await tools.query_itsm(container, question, session_id)

    @server.tool(
        description="Direct lookup of an incident by its number (e.g., 'INC0012345')."
    )
    def get_incident(number: str) -> dict[str, Any]:
        return tools.get_incident(container, number)

    @server.tool(
        description=(
            "Structured incident search. Use this when you have specific filters "
            "in mind and want to bypass the natural-language planner."
        )
    )
    def list_incidents(
        state: list[str] | None = None,
        priority: list[str] | None = None,
        category: str | None = None,
        assignee_name: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return tools.list_incidents(
            container,
            state=state,
            priority=priority,
            category=category,
            assignee_name=assignee_name,
            limit=limit,
        )

    @server.tool(description="Search knowledge base articles.")
    def search_kb(
        query: str, category: str | None = None, top_k: int = 3
    ) -> dict[str, Any]:
        return tools.search_kb(container, query, category, top_k)

    @server.tool(
        description=(
            "Propose a write operation (create or update incident). Returns a "
            "token and the proposed diff. Must be followed by confirm_write to apply."
        )
    )
    async def propose_write(
        question: str, session_id: str | None = None
    ) -> dict[str, Any]:
        return await tools.propose_write(container, question, session_id)

    @server.tool(description="Confirm or cancel a pending write proposal.")
    def confirm_write(token: str, confirm: bool = True) -> dict[str, Any]:
        return tools.confirm_write(container, token, confirm)

    return server


def main_stdio() -> None:
    """Entrypoint registered as `itsm-bridge-mcp` in pyproject.toml."""
    logging.basicConfig(level=logging.INFO)
    server = build_server()
    server.run(transport="stdio")


def main_sse() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    server = build_server()
    # FastMCP exposes a `sse_app()` method that returns an ASGI app.
    import uvicorn

    uvicorn.run(server.sse_app(), host=args.host, port=args.port)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio"
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    server = build_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        import uvicorn

        uvicorn.run(server.sse_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
