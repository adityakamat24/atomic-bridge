from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.planner.plan_schema import (
    AggregateOp,
    FindOp,
    KBLookupOp,
    OutputSpec,
    QueryPlan,
    ResolveOp,
    TraverseOp,
    WriteProposalOp,
)


def test_pydantic_rejects_unknown_op_at_parse_time() -> None:
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(
            {
                "intent": "lookup",
                "reasoning": "x" * 20,
                "operations": [{"op": "DROP_TABLE", "id": "boom"}],
                "output_spec": {"format": "list", "final_var": "boom"},
                "confidence": 0.9,
            }
        )


def test_traverse_op_accepts_from_alias() -> None:
    op = TraverseOp.model_validate(
        {"op": "traverse", "id": "i1", "from": "$u1", "relation": "x"}
    )
    assert op.from_var == "$u1"


def test_ambiguous_plan_must_have_no_operations() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            intent="ambiguous",
            reasoning="ambiguous because reasons",
            operations=[FindOp(id="x", entity="incident")],
            confidence=0.4,
            clarification_needed="please clarify",
        )


def test_ambiguous_plan_requires_clarification() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            intent="ambiguous",
            reasoning="ambiguous because reasons",
            operations=[],
            confidence=0.4,
        )


def test_out_of_scope_plan_must_have_no_operations() -> None:
    plan = QueryPlan(
        intent="out_of_scope",
        reasoning="not about ITSM",
        operations=[],
        confidence=0.99,
    )
    assert plan.operations == []


def test_non_terminal_plan_must_have_operations_and_output_spec() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            intent="lookup",
            reasoning="missing operations",
            operations=[],
            confidence=0.9,
            output_spec=OutputSpec(format="list", final_var="x"),
        )
    with pytest.raises(ValidationError):
        QueryPlan(
            intent="lookup",
            reasoning="missing output_spec",
            operations=[FindOp(id="x", entity="incident")],
            confidence=0.9,
        )


def test_find_op_limit_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        FindOp(id="x", entity="incident", limit=1001)


def test_kb_lookup_top_k_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        KBLookupOp(id="x", query="vpn", top_k=11)


def test_confidence_in_zero_to_one() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(
            intent="lookup",
            reasoning="confidence too high",
            operations=[FindOp(id="x", entity="incident")],
            output_spec=OutputSpec(format="list", final_var="x"),
            confidence=1.5,
        )


def test_full_round_trip_valid_lookup_plan() -> None:
    plan = QueryPlan(
        intent="lookup",
        reasoning="Need to find John Doe and his incidents",
        operations=[
            FindOp(id="u1", entity="sys_user"),
            TraverseOp.model_validate(
                {
                    "op": "traverse",
                    "id": "i1",
                    "from": "$u1",
                    "relation": "sys_user.incidentsReported",
                }
            ),
            ResolveOp(id="out", source="$i1", fields=["number", "state"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    assert plan.operations[0].op == "find"
    assert plan.operations[1].op == "traverse"
    assert plan.operations[2].op == "resolve"


def test_aggregate_op_top_n_carries_n() -> None:
    op = AggregateOp(
        id="a", source="$x", operation="top_n", group_by_field="category", n=3
    )
    assert op.n == 3


def test_write_proposal_op_constrains_action() -> None:
    op = WriteProposalOp(
        id="w", action="update_incident", target_var="$inc", fields={"state": "Closed"}
    )
    assert op.action == "update_incident"
