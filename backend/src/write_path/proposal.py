from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

WriteAction = Literal["create_incident", "update_incident"]


class WriteProposal(BaseModel):
    """Captured by the executor's write_proposal handler. The API stores
    these in an ApprovalStore (Phase 11) keyed by `token` and only mutates
    the data store after a confirm call."""

    token: str
    action: WriteAction
    target_sys_id: str | None = None
    target_display: str
    current_values: dict[str, Any] | None = None
    proposed_values: dict[str, Any]
    diff: dict[str, dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime
    nl_query: str = ""
    plan_id: str = ""


def new_proposal(
    *,
    action: WriteAction,
    proposed_values: dict[str, Any],
    target_display: str,
    target_sys_id: str | None = None,
    current_values: dict[str, Any] | None = None,
    diff: dict[str, dict[str, Any]] | None = None,
    nl_query: str = "",
    plan_id: str = "",
    ttl_seconds: int = 600,
) -> WriteProposal:
    now = datetime.now(UTC)
    return WriteProposal(
        token=str(uuid.uuid4()),
        action=action,
        target_sys_id=target_sys_id,
        target_display=target_display,
        current_values=current_values,
        proposed_values=proposed_values,
        diff=diff or {},
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        nl_query=nl_query,
        plan_id=plan_id,
    )
