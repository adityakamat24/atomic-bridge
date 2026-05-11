from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import AppContainer, get_container
from src.api.types import SessionCreateResponse, SessionState

router = APIRouter(prefix="/v1/session")


@router.post("")
async def create_session(
    container: AppContainer = Depends(get_container),
) -> SessionCreateResponse:
    state = await container.session_store.create()
    return SessionCreateResponse(session_id=state.session_id)


@router.get("/{sid}")
async def get_session(
    sid: str, container: AppContainer = Depends(get_container)
) -> SessionState:
    state = await container.session_store.get(sid)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found or expired")
    return state


@router.delete("/{sid}")
async def delete_session(
    sid: str, container: AppContainer = Depends(get_container)
) -> dict[str, str]:
    await container.session_store.delete(sid)
    return {"status": "deleted"}
