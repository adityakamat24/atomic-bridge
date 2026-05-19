from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.core.trace import ExecutionTrace
from src.planner.plan_schema import Intent, QueryPlan
from src.write_path.proposal import WriteProposal

Role = Literal["end_user", "agent", "manager", "admin"]


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    show_trace: bool = True
    show_plan: bool = True


class QueryResponse(BaseModel):
    request_id: str
    answer: str
    plan: QueryPlan | None = None
    trace: ExecutionTrace | None = None
    data: Any = None
    intent: Intent
    confidence: float
    clarification_needed: str | None = None
    write_proposal: WriteProposal | None = None
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int


class ConfirmRequest(BaseModel):
    token: str
    confirm: bool


class ConfirmResponse(BaseModel):
    status: Literal["confirmed", "cancelled", "expired", "conflict", "missing"]
    record: dict[str, Any] | None = None
    message: str | None = None


class SessionCreateRequest(BaseModel):
    role: Role | None = None
    as_user_sys_id: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    role: Role = "admin"
    actor_name: str | None = None
    user_sys_id: str | None = None


class PersonaSummary(BaseModel):
    sys_id: str | None = None
    role: Role
    name: str
    department: str | None = None
    member_groups: list[str] = Field(default_factory=list)
    direct_report_count: int = 0
    managed_group_count: int = 0
    is_synthetic_admin: bool = False


class SessionState(BaseModel):
    session_id: str
    user_sys_id: str | None = None
    actor_name: str | None = None
    role: Role = "admin"
    group_sys_ids: list[str] = Field(default_factory=list)
    direct_report_sys_ids: list[str] = Field(default_factory=list)
    managed_group_sys_ids: list[str] = Field(default_factory=list)
    # Pre-resolved at session-create; empty when role=admin (sentinel).
    visible_user_sys_ids: list[str] = Field(default_factory=list)
    turn_count: int = 0
    last_query: str = ""
    last_resolved_entities: list[dict[str, Any]] = Field(default_factory=list)
    last_plan: QueryPlan | None = None
    last_response: str = ""
    created_at: datetime
    last_active: datetime
