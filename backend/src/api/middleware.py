from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.cors import CORSMiddleware

logger = structlog.get_logger(__name__)


def install_middleware(app: FastAPI, allowed_origins: list[str]) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id_and_logging(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed", path=str(request.url.path))
            structlog.contextvars.unbind_contextvars("request_id")
            raise
        else:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "request_done",
                method=request.method,
                path=str(request.url.path),
                status=response.status_code,
                latency_ms=elapsed_ms,
            )
            response.headers["X-Request-ID"] = rid
            structlog.contextvars.unbind_contextvars("request_id")
            return response
