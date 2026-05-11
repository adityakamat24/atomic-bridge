from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.executor.trace import ExecutionTrace
from src.planner.plan_schema import Intent, QueryPlan
from src.write_path.proposal import WriteProposal


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


class SessionCreateResponse(BaseModel):
    session_id: str


class SessionState(BaseModel):
    session_id: str
    turn_count: int = 0
    last_query: str = ""
    last_resolved_entities: list[dict[str, Any]] = Field(default_factory=list)
    last_plan: QueryPlan | None = None
    last_response: str = ""
    created_at: datetime
    last_active: datetime
