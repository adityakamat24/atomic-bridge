from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    op_id: str
    op_type: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs_summary: str = ""
    outputs_count: int = 0
    latency_ms: int = 0
    warnings: list[str] = Field(default_factory=list)
    graph_traversal: list[str] = Field(default_factory=list)


class ExecutionTrace(BaseModel):
    request_id: str = ""
    plan_id: str = ""
    steps: list[TraceStep] = Field(default_factory=list)
    total_latency_ms: int = 0

    def to_visjs_highlights(self) -> list[str]:
        """Distinct relation IDs touched, in first-touched order. Used by the
        frontend graph view to highlight what the planner actually traversed.
        """
        seen: list[str] = []
        seen_set: set[str] = set()
        for step in self.steps:
            for rel in step.graph_traversal:
                if rel not in seen_set:
                    seen.append(rel)
                    seen_set.add(rel)
        return seen
