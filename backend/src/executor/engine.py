from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.data_store import DataStore
from src.core.schema_graph import SchemaGraph
from src.executor.operations import ExecutionContext, dispatch
from src.executor.reference_resolver import ReferenceResolver
from src.executor.trace import ExecutionTrace
from src.knowledge.retriever import KBRetriever
from src.planner.plan_schema import QueryPlan


class ExecutionError(Exception):
    """Raised when an operation fails for a structural reason — e.g. an
    undefined variable reference (which the validator should catch) or a
    handler crash."""


class ExecutionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    output: Any
    trace: ExecutionTrace
    plan: QueryPlan
    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        if self.output is None:
            return "no output"
        if isinstance(self.output, list):
            return f"list of {len(self.output)} item(s)"
        if isinstance(self.output, dict):
            return f"dict with keys {sorted(self.output.keys())}"
        return type(self.output).__name__


class ExecutionEngine:
    """Walks a validated `QueryPlan` op-by-op against a `DataStore`.

    Stateless across calls. KB retriever is optional — engines that don't
    handle knowledge queries can pass `kb=None`.
    """

    def __init__(
        self,
        graph: SchemaGraph,
        store: DataStore,
        kb: KBRetriever | None = None,
    ) -> None:
        self._graph = graph
        self._store = store
        self._kb = kb

    def execute(self, plan: QueryPlan, request_id: str = "") -> ExecutionResult:
        if plan.intent in ("ambiguous", "out_of_scope"):
            # Nothing to execute. Engine returns the plan as-is so the
            # response generator / API can render it.
            return ExecutionResult(
                output=None,
                trace=ExecutionTrace(request_id=request_id, plan_id=request_id),
                plan=plan,
            )

        ctx = ExecutionContext()
        resolver = ReferenceResolver(self._graph, self._store)
        trace = ExecutionTrace(request_id=request_id, plan_id=request_id)
        t_total = time.perf_counter()

        for op in plan.operations:
            try:
                result, step = dispatch(
                    op, ctx, self._graph, self._store, resolver, self._kb
                )
            except Exception as exc:  # noqa: BLE001
                raise ExecutionError(
                    f"op {op.id!r} ({op.op}) failed: {exc}"
                ) from exc
            ctx.bindings[op.id] = result
            trace.steps.append(step)

        trace.total_latency_ms = int((time.perf_counter() - t_total) * 1000)

        if plan.output_spec is None:
            output: Any = None
        else:
            final_var = plan.output_spec.final_var
            if final_var not in ctx.bindings:
                raise ExecutionError(
                    f"output_spec.final_var {final_var!r} was not produced by the plan"
                )
            output = self._project_output(plan, ctx.bindings[final_var])

        return ExecutionResult(
            output=output,
            trace=trace,
            plan=plan,
            warnings=list(ctx.warnings),
        )

    def _project_output(self, plan: QueryPlan, raw: Any) -> Any:
        spec = plan.output_spec
        if spec is None:
            return raw
        if spec.format == "single_item":
            if isinstance(raw, list):
                return raw[0] if raw else None
            return raw
        if spec.format == "list":
            if isinstance(raw, list):
                return raw[: spec.max_items_shown]
            return raw
        if spec.format == "scalar":
            if isinstance(raw, dict):
                # Aggregate result. Pull the first scalar value.
                for v in raw.values():
                    if not isinstance(v, list):
                        return v
            return raw
        # kb_answer and write_confirmation pass through untouched.
        return raw
