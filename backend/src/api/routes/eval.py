from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from src.api.deps import AppContainer, get_container

router = APIRouter(prefix="/v1/eval")


@router.post("/run")
async def run_eval(
    x_admin_token: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    if (
        not container.settings.ADMIN_TOKEN
        or x_admin_token != container.settings.ADMIN_TOKEN
    ):
        raise HTTPException(status_code=401, detail="admin token required")
    # The actual runner lives in `eval/runner.py` (Phase 13). For now,
    # report that the endpoint is wired and the queries file exists.
    return {
        "status": "endpoint_ready",
        "note": "Use `python -m eval.runner` for the full run (Phase 13).",
    }
