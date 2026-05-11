from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import AppContainer, get_container

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    if not container.is_ready:
        raise HTTPException(status_code=503, detail="not ready")
    return {
        "status": "ready",
        "kb_size": container.kb_retriever.size,
        "schema_entities": len(container.graph.all_entities()),
    }
