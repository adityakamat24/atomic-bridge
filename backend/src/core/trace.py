"""Execution-trace and result types.

Lives in ``core`` because ``SchemaGraph.execute_plan`` is the producer
and the API layer is the consumer; the executor module that used to own
these is deleted in step 10 of the rewrite. The legacy
``executor/trace.py`` re-exports these names so any remaining imports
keep working during the transition.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TraceStep(BaseModel):
    op_id: str
    op_type: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs_summary: str = ""
    outputs_count: int = 0
    latency_ms: int = 0
    warnings: list[str] = Field(default_factory=list)
    graph_traversal: list[str] = Field(default_factory=list)
    target_entity: str = ""
    hops_filtered: list[str] = Field(default_factory=list)
    # Populated when SchemaGraph.walk had to call the RelationScorer to
    # rank multiple candidate chains. None means either the planner
    # supplied an explicit path or only one candidate chain existed.
    scored_alternatives: list[dict[str, Any]] | None = None
    scoring_latency_ms: int | None = None
    scoring_reasoning: str | None = None
    scoring_confidence: float | None = None
    # Every chain the engine attempted, in ranking order. Each entry:
    # {rank, candidate_index, path, records_count, used}. The "used"
    # entry is the chain whose records made it into the final output —
    # may not be the rank-0 entry if the top-ranked chain returned empty
    # and the engine fell back to the next-ranked. Empty when the
    # planner supplied an explicit path or only one candidate existed.
    attempted_paths: list[dict[str, Any]] | None = None


class ExecutionTrace(BaseModel):
    request_id: str = ""
    plan_id: str = ""
    steps: list[TraceStep] = Field(default_factory=list)
    total_latency_ms: int = 0

    def to_visjs_highlights(self) -> list[str]:
        """Distinct relation IDs touched, in first-touched order. The
        frontend graph view uses this to highlight what the executor
        actually traversed."""
        seen: list[str] = []
        seen_set: set[str] = set()
        for step in self.steps:
            for rel in step.graph_traversal:
                if rel not in seen_set:
                    seen.append(rel)
                    seen_set.add(rel)
        return seen


class ExecutionResult(BaseModel):
    """What ``SchemaGraph.execute_plan`` returns.

    ``output`` is the projected final result (per the plan's output_spec).
    ``trace`` records each op's inputs, outputs summary, latency, and
    graph relations touched. ``warnings`` collects dangling-ref and
    no-path messages surfaced during execution.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    output: Any = None
    trace: ExecutionTrace
    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        if self.output is None:
            return "no output"
        if isinstance(self.output, list):
            return f"list of {len(self.output)} item(s)"
        if isinstance(self.output, dict):
            return f"dict with keys {sorted(self.output.keys())}"
        return type(self.output).__name__


class ExecutionError(Exception):
    """Structural failure during plan execution — undefined variable
    reference, missing target_var on update_incident, etc. The validator
    catches most of these earlier; this is the runtime safety net."""
