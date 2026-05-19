"""POST /v1/query — the natural-language entry point.

Pipeline (steady state after the rewrite):

  input_validator -> rate_limit -> injection_detector (advisory) ->
  Planner (LLM #1) -> graph.execute_plan -> Responder (LLM #2) ->
  output_filter scan -> audit log -> response.

Short-circuits:
  * intent=out_of_scope     -> canned refusal, no execution, no responder
  * intent=ambiguous        -> clarification, no execution, no responder
  * intent=write_proposal   -> execute, store proposal, return diff text;
                              the responder is not invoked because the
                              answer is a structured diff the frontend
                              renders directly.
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.deps import AppContainer, get_container
from src.api.rate_limit import RateLimitExceededError
from src.api.types import QueryRequest, QueryResponse
from src.core.trace import ExecutionError
from src.guardrails.input_validator import InputRejected
from src.guardrails.output_filter import OutputFilter
from src.guardrails.view_scope import ViewScope
from src.planner.plan_validator import PlanValidationError
from src.planner.planner import PriorTurn

router = APIRouter(prefix="/v1")
logger = structlog.get_logger(__name__)


@router.post("/query", response_model=QueryResponse)
async def submit_query(
    req: QueryRequest,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> QueryResponse:
    request_id = getattr(request.state, "request_id", "")
    client_ip = request.client.host if request.client else "anon"

    try:
        await container.rate_limiter.check(client_ip)
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    try:
        validated = container.input_validator.validate(req.query)
    except InputRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    t0 = time.perf_counter()
    injection_score = container.injection_detector.score(validated.text)

    # Prior turn for pronoun resolution + per-request view scope.
    prior: PriorTurn | None = None
    session = None
    scope = ViewScope.admin()
    if req.session_id:
        session = await container.session_store.get(req.session_id)
        if session and session.last_query:
            prior = PriorTurn(
                query=session.last_query,
                resolved_entities=tuple(
                    e.get("resolved_sys_id") or e.get("surface", "")
                    for e in session.last_resolved_entities
                    if isinstance(e, dict)
                ),
            )
        if session:
            scope = ViewScope.from_session(session)

    # Per-request OutputFilter: admin sees sensitive fields verbatim,
    # everyone else gets them redacted.
    request_output_filter = OutputFilter.from_view_scope(container.graph, scope)

    # LLM call 1: planner.
    try:
        plan = await container.planner.plan(
            validated.text, prior_turn=prior, view_scope=scope,
        )
    except PlanValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "plan_validation_failed", "errors": exc.errors},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("planner_failed")
        raise HTTPException(
            status_code=500, detail=f"planner failed: {exc}",
        ) from exc

    # out_of_scope: prefer the planner's clarification over the generic
    # ITSM-only fallback so role-scoped refusals read better.
    if plan.intent == "out_of_scope":
        elapsed = int((time.perf_counter() - t0) * 1000)
        await _audit(
            container, request_id, req, validated.text,
            injection_score, plan, "out_of_scope", elapsed,
        )
        message = plan.clarification_needed or (
            "I can only help with IT service questions about tickets, "
            "users, teams, and knowledge articles."
        )
        return QueryResponse(
            request_id=request_id,
            answer=message,
            plan=plan if req.show_plan else None,
            intent="out_of_scope",
            confidence=1.0,
            clarification_needed=plan.clarification_needed,
            latency_ms=elapsed,
        )

    # ambiguous: surface the planner's clarification; no execution.
    if plan.intent == "ambiguous":
        elapsed = int((time.perf_counter() - t0) * 1000)
        await _audit(
            container, request_id, req, validated.text,
            injection_score, plan, "ambiguous", elapsed,
        )
        return QueryResponse(
            request_id=request_id,
            answer=plan.clarification_needed or "I need more detail to answer.",
            plan=plan if req.show_plan else None,
            intent="ambiguous",
            confidence=plan.confidence,
            clarification_needed=plan.clarification_needed,
            latency_ms=elapsed,
        )

    # Everything else: run on the graph executor. execute_plan is async
    # because the relation scorer (a third LLM ROLE on the response-side
    # client) is invoked at execution time when the planner emits a
    # declarative traverse with no explicit path AND multiple shortest
    # chains exist between source and target.
    try:
        result = await container.graph.execute_plan(
            plan,
            query=validated.text,
            request_id=request_id,
            scorer=container.relation_scorer,
            view_scope=scope,
        )
    except ExecutionError as exc:
        logger.exception("execute_plan_failed")
        raise HTTPException(
            status_code=500, detail=f"execution failed: {exc}",
        ) from exc

    # write_proposal: structured answer, no responder LLM call.
    if plan.intent == "write_proposal":
        proposal = result.output
        if proposal is not None:
            container.approval_store.put(proposal)
        elapsed = int((time.perf_counter() - t0) * 1000)
        await _audit(
            container, request_id, req, validated.text,
            injection_score, plan, "write_proposed", elapsed,
        )
        diff_str = (
            ", ".join(
                f"{k}: {v['from']!r} -> {v['to']!r}"
                for k, v in proposal.diff.items()
            )
            if proposal else "(no proposal generated)"
        )
        target = proposal.target_display if proposal else "unknown"
        return QueryResponse(
            request_id=request_id,
            answer=f"Proposed change to {target}: {diff_str}. Confirm to apply.",
            plan=plan if req.show_plan else None,
            trace=result.trace if req.show_trace else None,
            intent="write_proposal",
            confidence=plan.confidence,
            write_proposal=proposal,
            warnings=result.warnings,
            latency_ms=elapsed,
        )

    # Read path: PII filter the data, then LLM call 2 (responder).
    data: Any = result.output
    if isinstance(data, list):
        data = [
            _filter_record(request_output_filter, r, _record_entity(plan))
            for r in data
        ]

    answer = await container.responder.respond(validated.text, plan, result)
    answer, leak_warnings = container.output_filter.scan_response_text(answer)
    if leak_warnings:
        result.warnings.extend(leak_warnings)

    elapsed = int((time.perf_counter() - t0) * 1000)
    await _audit(
        container, request_id, req, validated.text,
        injection_score, plan, "read_ok", elapsed,
    )

    # Session update for pronoun resolution next turn.
    if req.session_id and session:
        await container.session_store.update(
            req.session_id,
            last_query=validated.text,
            # Store just the entity IDs the plan touched, derived from
            # find ops. Saves us from carrying every record through.
            last_resolved_entities=[
                {"surface": op.entity, "resolved_sys_id": ""}
                for op in plan.operations
                if hasattr(op, "entity")
            ],
            last_plan=plan,
            last_response=answer,
            turn_count=session.turn_count + 1,
        )

    return QueryResponse(
        request_id=request_id,
        answer=answer,
        plan=plan if req.show_plan else None,
        trace=result.trace if req.show_trace else None,
        data=data,
        intent=plan.intent,
        confidence=plan.confidence,
        warnings=result.warnings,
        latency_ms=elapsed,
    )


def _record_entity(plan: object) -> str:
    """Best-effort: pick the first FindOp's entity for the per-record
    output filter. Multi-entity plans will use whichever entity matched
    first; the filter is permissive (skips unknown fields)."""
    ops = getattr(plan, "operations", [])
    for op in ops:
        if getattr(op, "op", None) == "find":
            return str(getattr(op, "entity", ""))
    return ""


def _filter_record(
    filt: OutputFilter, record: dict[str, Any], entity: str,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        return record
    if not entity:
        return {
            k: v for k, v in record.items()
            if not (k == "sys_id" or k.endswith("_sys_id"))
        }
    return filt.filter_record(record, entity)


async def _audit(
    container: AppContainer,
    request_id: str,
    req: QueryRequest,
    text: str,
    injection_score: float,
    plan: object | None,
    outcome: str,
    latency_ms: int,
) -> None:
    container.audit_log.write(
        {
            "event": "query",
            "request_id": request_id,
            "session_id": req.session_id,
            "user_query": text,
            "injection_score": injection_score,
            "plan": (
                getattr(plan, "model_dump", lambda: None)() if plan else None
            ),
            "outcome": outcome,
            "latency_ms": latency_ms,
        }
    )
