"""MCP tool implementations.

Thin wrappers over the same internal services the FastAPI routes use.
After the rewrite, the read pipeline is two LLM calls (Planner +
Responder); the structured tools (get_incident, list_incidents,
search_kb) bypass both and talk to the graph directly.

Six tools (08-mcp-server.md):
  - query_itsm: NL entry point
  - get_incident: structured lookup by number
  - list_incidents: structured search
  - search_kb: KB search
  - propose_write: build a write proposal
  - confirm_write: confirm or cancel a pending proposal
"""

from __future__ import annotations

import time
from typing import Any

from src.api.deps import AppContainer
from src.core.data_store import Filter
from src.core.trace import ExecutionError
from src.guardrails.input_validator import InputRejected
from src.planner.plan_validator import PlanValidationError
from src.planner.planner import PriorTurn
from src.write_path.executor import (
    ProposalConflict,
    ProposalExpired,
    ProposalMissing,
    WriteExecutionError,
    confirm,
)


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **payload}


def _err(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "details": details}}


# ---------- query_itsm (NL) ------------------------------------------------


async def query_itsm(
    container: AppContainer, question: str, session_id: str | None = None,
) -> dict[str, Any]:
    """Full NL pipeline. Two LLM calls (planner + responder) for read
    queries; one LLM call (planner) for short-circuits and write
    proposals. Mirrors api/routes/query.py."""
    t0 = time.perf_counter()
    try:
        validated = container.input_validator.validate(question)
    except InputRejected as exc:
        return _err("INPUT_REJECTED", str(exc))

    prior: PriorTurn | None = None
    if session_id:
        s = await container.session_store.get(session_id)
        if s and s.last_query:
            prior = PriorTurn(
                query=s.last_query,
                resolved_entities=tuple(
                    e.get("resolved_sys_id") or e.get("surface", "")
                    for e in s.last_resolved_entities
                    if isinstance(e, dict)
                ),
            )

    try:
        plan = await container.planner.plan(validated.text, prior_turn=prior)
    except PlanValidationError as exc:
        return _err(
            "PLAN_VALIDATION_FAILED", "; ".join(exc.errors), errors=exc.errors,
        )

    if plan.intent == "out_of_scope":
        return _ok({
            "intent": "out_of_scope",
            "answer": "I can only help with IT service questions.",
            "plan": plan.model_dump(),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        })

    if plan.intent == "ambiguous":
        return _ok({
            "intent": "ambiguous",
            "answer": plan.clarification_needed,
            "plan": plan.model_dump(),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        })

    try:
        result = await container.graph.execute_plan(
            plan, query=validated.text,
            scorer=container.relation_scorer,
        )
    except ExecutionError as exc:
        return _err("EXECUTION_FAILED", str(exc))

    if plan.intent == "write_proposal" and result.output is not None:
        container.approval_store.put(result.output)
        return _ok({
            "intent": "write_proposal",
            "write_proposal": result.output.model_dump(mode="json"),
            "plan": plan.model_dump(),
            "trace": result.trace.model_dump(),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        })

    answer = await container.responder.respond(validated.text, plan, result)
    answer, leak_warnings = container.output_filter.scan_response_text(answer)
    return _ok({
        "intent": plan.intent,
        "answer": answer,
        "data": result.output,
        "plan": plan.model_dump(),
        "trace": result.trace.model_dump(),
        "warnings": result.warnings + leak_warnings,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    })


# ---------- get_incident ---------------------------------------------------


def get_incident(container: AppContainer, number: str) -> dict[str, Any]:
    matches = container.store.find(
        "incident",
        [Filter(field="number", operator="eq", value=number)],
        limit=1,
    )
    if not matches:
        return _err("NOT_FOUND", f"incident {number!r} not found")
    rec = matches[0]
    return _ok({"incident": _project_incident(container, rec)})


def list_incidents(
    container: AppContainer,
    state: list[str] | None = None,
    priority: list[str] | None = None,
    category: str | None = None,
    assignee_name: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    filters: list[Filter] = []
    if state:
        filters.append(Filter(field="state", operator="in", value=list(state)))
    if priority:
        filters.append(
            Filter(field="priority", operator="in", value=list(priority)),
        )
    if category:
        filters.append(Filter(field="category", operator="eq", value=category))
    if assignee_name:
        # Store-side fuzzy: use `contains` against the user table. No
        # application-layer pre-indexed fuzzy matcher in the new design.
        users = container.store.find(
            "sys_user",
            [Filter(field="name", operator="contains", value=assignee_name)],
            limit=10,
        )
        if users:
            filters.append(
                Filter(
                    field="assigned_to", operator="in",
                    value=[u["sys_id"] for u in users],
                )
            )
    rows = container.store.find("incident", filters, limit=limit)
    return _ok({"incidents": [_project_incident(container, r) for r in rows]})


def _project_incident(
    container: AppContainer, rec: dict[str, Any],
) -> dict[str, Any]:
    g = container.graph
    out = {}
    for k, v in rec.items():
        if k.endswith("sys_id") or k == "sys_id":
            continue
        if k in ("state", "priority") and isinstance(v, int):
            field = g.field(f"incident.{k}")
            if field.value_map_id:
                vm = g.value_map(field.value_map_id)
                out[k] = vm.forward.get(v, v)
                continue
        out[k] = v
    return out


# ---------- search_kb ------------------------------------------------------


def search_kb(
    container: AppContainer,
    query: str,
    category: str | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    hits = container.kb_retriever.search(query, category_hint=category, top_k=top_k)
    return _ok({"articles": hits})


# ---------- propose_write / confirm_write --------------------------------


async def propose_write(
    container: AppContainer,
    question: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    out = await query_itsm(container, question, session_id=session_id)
    if not out.get("ok"):
        return out
    if out.get("intent") != "write_proposal":
        return _err(
            "NOT_A_WRITE",
            f"intent was {out.get('intent')!r}; use query_itsm for read queries",
        )
    return out


def confirm_write(
    container: AppContainer, token: str, confirm_flag: bool = True,
) -> dict[str, Any]:
    try:
        status, record = confirm(
            token=token,
            confirm_flag=confirm_flag,
            store=container.store,
            graph=container.graph,
            approvals=container.approval_store,
        )
    except ProposalMissing as exc:
        return _err("PROPOSAL_MISSING", str(exc))
    except ProposalExpired as exc:
        return _err("PROPOSAL_EXPIRED", str(exc))
    except ProposalConflict as exc:
        return _err("PROPOSAL_CONFLICT", str(exc))
    except WriteExecutionError as exc:
        return _err("WRITE_FAILED", str(exc))
    return _ok({"status": status, "record": record})
