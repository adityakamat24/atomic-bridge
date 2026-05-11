from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.deps import build_container
from src.api.middleware import install_middleware
from src.api.routes import eval as eval_routes
from src.api.routes import health, query, schema, session, write
from src.config import get_settings
from src.observability.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # build_container loads schema, KB index, and instantiates LLM clients.
    # If LLM env vars are missing this will raise — the API caller can still
    # use mock-injected containers via app.state.container in tests.
    if not hasattr(app.state, "container") or app.state.container is None:
        app.state.container = build_container(settings)
    yield


app = FastAPI(
    title="itsm-bridge",
    version="0.1.0",
    description="Natural-language mediation layer over ServiceNow-shaped IT data",
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
