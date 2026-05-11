from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import AppContainer, get_container
from src.api.types import ConfirmRequest, ConfirmResponse
from src.write_path.executor import (
    ProposalConflict,
    ProposalExpired,
    ProposalMissing,
    WriteExecutionError,
    confirm,
)

router = APIRouter(prefix="/v1/write")
logger = structlog.get_logger(__name__)


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_write(
    req: ConfirmRequest,
    container: AppContainer = Depends(get_container),
) -> ConfirmResponse:
    try:
        status, record = confirm(
            token=req.token,
            confirm_flag=req.confirm,
            store=container.store,
            graph=container.graph,
            approvals=container.approval_store,
        )
    except ProposalMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProposalExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ProposalConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WriteExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    container.audit_log.write(
        {
            "event": "write_confirmed" if status == "confirmed" else "write_cancelled",
            "token": req.token,
            "record": record,
        }
    )
    return ConfirmResponse(status=status, record=record)  # type: ignore[arg-type]
