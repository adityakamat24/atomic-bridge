from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.deps import AppContainer, get_container
from src.api.rate_limit import RateLimitExceededError
from src.api.types import QueryRequest, QueryResponse
from src.guardrails.input_validator import InputRejected
from src.planner.plan_validator import PlanValidationError
from src.planner.preprocessor import PriorTurn

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

    # Session-aware prior turn for pronoun resolution.
    prior = PriorTurn()
    session = None
    if req.session_id:
        session = await container.session_store.get(req.session_id)
        if session:
            prior = PriorTurn(
                query=session.last_query,
                resolved_entities=session.last_resolved_entities,
            )

    try:
        pre_out = await container.preprocessor.process(validated.text, prior=prior)
    except Exception as exc:  # noqa: BLE001
        logger.exception("preprocessor_failed")
        raise HTTPException(status_code=500, detail=f"preprocessor failed: {exc}") from exc

    if pre_out.intent == "out_of_scope":
        elapsed = int((time.perf_counter() - t0) * 1000)
        await _audit(
            container,
            request_id,
            req,
            validated.text,
            injection_score,
            None,
            "out_of_scope",
            elapsed,
        )
        return QueryResponse(
            request_id=request_id,
            answer="I can only help with IT service questions about tickets, users, teams, and knowledge articles.",
            intent="out_of_scope",
            confidence=1.0,
            latency_ms=elapsed,
        )

    try:
        plan = await container.plan_generator.generate(
            question=validated.text,
            rewritten_query=pre_out.rewritten_query,
            resolved_entities=pre_out.entity_mentions,
            subgraph_ids=pre_out.relevant_entities or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("plan_generator_failed")
        raise HTTPException(status_code=500, detail=f"plan generator failed: {exc}") from exc

    try:
        plan = container.plan_validator.validate(plan)
    except PlanValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "plan_validation_failed", "errors": exc.errors},
        ) from exc

    if plan.intent == "ambiguous":
        elapsed = int((time.perf_counter() - t0) * 1000)
        await _audit(
            container,
            request_id,
            req,
            validated.text,
            injection_score,
            plan,
            "ambiguous",
            elapsed,
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

    if plan.intent == "write_proposal":
        result = container.executor.execute(plan, request_id=request_id)
        proposal = result.output
        if proposal is not None:
            container.approval_store.put(proposal)
        elapsed = int((time.perf_counter() - t0) * 1000)
        await _audit(
            container,
            request_id,
            req,
            validated.text,
            injection_score,
            plan,
            "write_proposed",
            elapsed,
        )
        diff_str = (
            ", ".join(f"{k}: {v['from']!r} -> {v['to']!r}" for k, v in proposal.diff.items())
            if proposal
            else "(no proposal generated)"
        )
        answer = f"Proposed change to {proposal.target_display if proposal else 'unknown'}: {diff_str}. Confirm to apply."
        return QueryResponse(
            request_id=request_id,
            answer=answer,
            plan=plan if req.show_plan else None,
            trace=result.trace if req.show_trace else None,
            intent="write_proposal",
            confidence=plan.confidence,
            write_proposal=proposal,
            warnings=result.warnings,
            latency_ms=elapsed,
        )

    # READ PATH ----------------------------------------------------------
    result = container.executor.execute(plan, request_id=request_id)

    # PII filter on each record (defence-in-depth; validator already stripped).
    data: Any = result.output
    if isinstance(data, list):
        data = [_filter_record(container, r, _record_entity(plan)) for r in data]

    answer = await container.response_generator.generate(validated.text, result)
    answer, leak_warnings = container.output_filter.scan_response_text(answer)
    if leak_warnings:
        result.warnings.extend(leak_warnings)

    elapsed = int((time.perf_counter() - t0) * 1000)
    await _audit(
        container,
        request_id,
        req,
        validated.text,
        injection_score,
        plan,
        "read_ok",
        elapsed,
    )

    if req.session_id and session:
        await container.session_store.update(
            req.session_id,
            last_query=validated.text,
            last_resolved_entities=[m.model_dump() for m in pre_out.entity_mentions],
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
    """Best-effort: pick the first FindOp's entity. Used only for the
    output filter's per-record sensitivity check."""
    ops = getattr(plan, "operations", [])
    for op in ops:
        if getattr(op, "op", None) == "find":
            return str(getattr(op, "entity", ""))
    return ""


def _filter_record(container: AppContainer, record: dict[str, Any], entity: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        return record
    if not entity:
        # Just strip sys_id-like fields
        return {k: v for k, v in record.items() if not (k == "sys_id" or k.endswith("_sys_id"))}
    return container.output_filter.filter_record(record, entity)


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
            "plan": getattr(plan, "model_dump", lambda: None)() if plan else None,
            "outcome": outcome,
            "latency_ms": latency_ms,
        }
    )
