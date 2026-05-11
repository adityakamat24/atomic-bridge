from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from src.api.deps import build_container
from src.api.middleware import install_middleware
from src.api.routes import eval as eval_routes
from src.api.routes import health, query, schema, session, write
from src.config import get_settings
from src.observability.logging import configure_logging

configure_logging()


async def _warm_up(container: Any) -> None:
    """Background warm-up: pre-load the embedding model and pre-build the
    KB index so the FIRST user query doesn't pay the cold-start cost.

    Lifespan returns instantly (health check passes), then this runs in the
    background. Users typically take 5-30s between page open and first query
    — by then warming is done. If a query arrives mid-warmup, the lazy
    `build_index()` and `_ensure_embedded()` paths will block on the same
    work; result is correct, just slower for that one query.
    """
    loop = asyncio.get_event_loop()
    try:
        print("[warmup] starting (embedding model + KB index + few-shot)", flush=True)
        await loop.run_in_executor(None, container.kb_retriever.build_index)
        # Also warm few-shot embeddings (uses the same model that's now loaded).
        await loop.run_in_executor(
            None, container.few_shot._ensure_embedded
        )
        print(
            f"[warmup] complete · kb_size={container.kb_retriever.size}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warmup] FAILED: {exc!r}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not hasattr(app.state, "container") or app.state.container is None:
        app.state.container = build_container(settings)

    # MCP runs as a separate process on port 8001 (see scripts/start.sh).
    # Mounting FastMCP's sse_app on a sub-path behind a proxy returns
    # "Invalid Host header" — keep the two cleanly separated instead.

    # Kick off warm-up in the background — does NOT block lifespan,
    # so health checks pass within a couple of seconds even if the model
    # takes 10-15s to load.
    asyncio.create_task(_warm_up(app.state.container))

    yield


app = FastAPI(
    title="Atomic Bridge",
    version="0.1.0",
    description=(
        "Natural-language mediation layer over ServiceNow-shaped IT data — "
        "the architectural core behind the Atom AI agent."
    ),
    lifespan=lifespan,
)

install_middleware(app, allowed_origins=get_settings().cors_origins)

# Routers
app.include_router(health.router)
app.include_router(schema.router)
app.include_router(session.router)
app.include_router(query.router)
app.include_router(write.router)
app.include_router(eval_routes.router)
