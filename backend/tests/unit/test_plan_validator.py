from __future__ import annotations

from pathlib import Path

import pytest

from src.core.data_store import Filter
from src.core.schema_graph import SchemaGraph
from src.core.schema_loader import load
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
from src.planner.plan_validator import (
    MAX_OPERATIONS,
    PlanValidationError,
    PlanValidator,
)

REPO_DATA_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"


@pytest.fixture
def graph() -> SchemaGraph:
    return load(REPO_DATA_SCHEMA)


@pytest.fixture
def validator(graph: SchemaGraph) -> PlanValidator:
    return PlanValidator(graph, allow_pii=False)


def _plan(*ops, intent="lookup", final_var="out") -> QueryPlan:  # type: ignore[no-untyped-def]
    return QueryPlan(
        intent=intent,
        reasoning="x" * 20,
        operations=list(ops),
        output_spec=OutputSpec(format="list", final_var=final_var),
        confidence=0.9,
    )


# ---------- Failure category 1: nonexistent entity --------------------------


def test_rejects_nonexistent_entity(validator: PlanValidator) -> None:
    plan = _plan(FindOp(id="x", entity="not_a_table"), final_var="x")
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("not_a_table" in e for e in exc.value.errors)


# ---------- Failure category 2: nonexistent field ---------------------------


def test_rejects_nonexistent_field_in_filter(validator: PlanValidator) -> None:
    plan = _plan(
        FindOp(
            id="x",
            entity="incident",
            filters=[Filter(field="not_a_field", operator="eq", value=2)],
        ),
        final_var="x",
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("not_a_field" in e for e in exc.value.errors)


# ---------- Failure category 3: nonexistent relation ------------------------


def test_rejects_nonexistent_relation(validator: PlanValidator) -> None:
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp.model_validate(
            {
                "op": "traverse",
                "id": "x",
                "from": "$u",
                "to_entity": "incident",
                "path": ["no_such_rel"],
            }
        ),
        final_var="x",
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("no_such_rel" in e for e in exc.value.errors)


# ---------- Failure category 4: undefined $var reference --------------------


def test_rejects_traverse_from_undefined_variable(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        TraverseOp.model_validate(
            {
                "op": "traverse",
                "id": "x",
                "from": "$ghost",
                "to_entity": "sys_user",
                "path": ["incident.reportedBy"],
            }
        ),
        final_var="x",
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("ghost" in e for e in exc.value.errors)


def test_rejects_aggregate_with_undefined_source(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        AggregateOp(id="cnt", source="$missing", operation="count"),
        final_var="cnt",
    )
    with pytest.raises(PlanValidationError):
        validator.validate(plan)


def test_rejects_output_spec_referencing_undefined_var(
    validator: PlanValidator,
) -> None:
    plan = _plan(FindOp(id="x", entity="incident"), final_var="ghost")
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("ghost" in e for e in exc.value.errors)


# ---------- Failure category 5: resource limits exceeded --------------------


def test_rejects_more_than_max_operations(validator: PlanValidator) -> None:
    ops = [FindOp(id=f"f{i}", entity="incident") for i in range(MAX_OPERATIONS + 1)]
    plan = _plan(*ops, final_var=f"f{MAX_OPERATIONS}")
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("too many operations" in e for e in exc.value.errors)


def test_rejects_more_than_max_traverse_ops(validator: PlanValidator) -> None:
    ops: list = [FindOp(id="u", entity="sys_user")]
    for i in range(6):  # 6 traverse ops > limit of 5
        ops.append(
            TraverseOp.model_validate(
                {
                    "op": "traverse",
                    "id": f"t{i}",
                    "from": "$u",
                    "to_entity": "incident",
                    "path": ["sys_user.incidentsReported"],
                }
            )
        )
    plan = _plan(*ops, final_var="t5")
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("traverse ops" in e for e in exc.value.errors)


# ---------- Failure category 6: write_proposal must be terminal -------------


def test_rejects_traverse_after_write_proposal(validator: PlanValidator) -> None:
    plan = _plan(
        FindOp(id="inc", entity="incident"),
        WriteProposalOp(
            id="prop",
            action="update_incident",
            target_var="$inc",
            fields={"state": "Closed"},
        ),
        TraverseOp.model_validate(
            {
                "op": "traverse",
                "id": "x",
                "from": "$inc",
                "to_entity": "sys_user",
                "path": ["incident.reportedBy"],
            }
        ),
        final_var="x",
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("write_proposal must be the last" in e for e in exc.value.errors)


# ---------- Value-map enforcement --------------------------------------------


def test_rejects_filter_value_not_in_value_map(validator: PlanValidator) -> None:
    plan = _plan(
        FindOp(
            id="x",
            entity="incident",
            filters=[Filter(field="state", operator="eq", value="Definitely Not A State")],
        ),
        final_var="x",
    )
    with pytest.raises(PlanValidationError):
        validator.validate(plan)


def test_translates_display_string_to_code_after_validation(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        FindOp(
            id="x",
            entity="incident",
            filters=[Filter(field="state", operator="eq", value="In Progress")],
        ),
        final_var="x",
    )
    out = validator.validate(plan)
    assert out.operations[0].filters[0].value == 2


def test_translates_value_map_inside_list_filter(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        FindOp(
            id="x",
            entity="incident",
            filters=[
                Filter(
                    field="state",
                    operator="in",
                    value=["New", "In Progress", "On Hold"],
                )
            ],
        ),
        final_var="x",
    )
    out = validator.validate(plan)
    assert out.operations[0].filters[0].value == [1, 2, 3]


def test_passes_through_integer_codes_unchanged(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        FindOp(
            id="x",
            entity="incident",
            filters=[Filter(field="state", operator="eq", value=2)],
        ),
        final_var="x",
    )
    out = validator.validate(plan)
    assert out.operations[0].filters[0].value == 2


# ---------- PII stripping ---------------------------------------------------


def test_strips_email_field_when_pii_disallowed(graph: SchemaGraph) -> None:
    v = PlanValidator(graph, allow_pii=False)
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        ResolveOp(id="out", source="$u", fields=["name", "email", "department"]),
    )
    out = v.validate(plan)
    final = out.operations[1]
    assert isinstance(final, ResolveOp)
    assert "email" not in final.fields
    assert {"name", "department"} <= set(final.fields)


def test_keeps_email_field_when_pii_allowed(graph: SchemaGraph) -> None:
    v = PlanValidator(graph, allow_pii=True)
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        ResolveOp(id="out", source="$u", fields=["name", "email"]),
    )
    out = v.validate(plan)
    final = out.operations[1]
    assert isinstance(final, ResolveOp)
    assert "email" in final.fields


# ---------- Write proposal field allowlist ----------------------------------


def test_rejects_write_with_disallowed_fields(validator: PlanValidator) -> None:
    plan = _plan(
        FindOp(id="inc", entity="incident"),
        WriteProposalOp(
            id="prop",
            action="update_incident",
            target_var="$inc",
            fields={"sys_id": "evil"},
        ),
        intent="write_proposal",
        final_var="prop",
    )
    plan = plan.model_copy(
        update={"output_spec": OutputSpec(format="write_confirmation", final_var="prop")}
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("disallowed fields" in e for e in exc.value.errors)


def test_update_incident_requires_target_var(validator: PlanValidator) -> None:
    plan = _plan(
        WriteProposalOp(id="prop", action="update_incident", fields={"state": "Closed"}),
        intent="write_proposal",
        final_var="prop",
    )
    plan = plan.model_copy(
        update={"output_spec": OutputSpec(format="write_confirmation", final_var="prop")}
    )
    with pytest.raises(PlanValidationError):
        validator.validate(plan)


# ---------- Happy path ------------------------------------------------------


def test_valid_multi_hop_lookup_passes(validator: PlanValidator) -> None:
    plan = _plan(
        FindOp(
            id="u",
            entity="sys_user",
            filters=[Filter(field="department", operator="eq", value="Engineering")],
        ),
        TraverseOp.model_validate(
            {
                "op": "traverse",
                "id": "i",
                "from": "$u",
                "to_entity": "incident",
                "path": ["sys_user.incidentsReported"],
            }
        ),
        ResolveOp(id="out", source="$i", fields=["number", "state", "priority"]),
        final_var="out",
        intent="cross_reference",
    )
    out = validator.validate(plan)
    assert len(out.operations) == 3


def test_kb_lookup_plan_passes(validator: PlanValidator) -> None:
    plan = _plan(
        KBLookupOp(id="kb", query="outlook crashes", category_hint="Software"),
        ResolveOp(id="out", source="$kb", fields=["number", "short_description", "text"]),
        intent="knowledge",
        final_var="out",
    )
    plan = plan.model_copy(
        update={"output_spec": OutputSpec(format="kb_answer", final_var="out")}
    )
    out = validator.validate(plan)
    assert isinstance(out.operations[0], KBLookupOp)


def test_ambiguous_plan_passes_through_validator(validator: PlanValidator) -> None:
    plan = QueryPlan(
        intent="ambiguous",
        reasoning="user gave too little detail",
        operations=[],
        confidence=0.4,
        clarification_needed="What entity did you mean?",
    )
    out = validator.validate(plan)
    assert out.intent == "ambiguous"


def test_out_of_scope_plan_passes_through_validator(
    validator: PlanValidator,
) -> None:
    plan = QueryPlan(
        intent="out_of_scope",
        reasoning="not an ITSM question",
        operations=[],
        confidence=0.99,
    )
    out = validator.validate(plan)
    assert out.intent == "out_of_scope"


# ---------- Declarative TraverseOp (new shape) ------------------------------


def test_declarative_traverse_single_hop_validates(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="inc",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.incidentsReported"],
        ),
        ResolveOp(id="out", source="$inc", fields=["number"]),
    )
    out = validator.validate(plan)
    assert out.operations[1].to_entity == "incident"


def test_declarative_traverse_three_hop_through_kb_bridge(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="kb",
            **{"from": "$u"},
            to_entity="kb_knowledge",
            path=[
                "sys_user.incidentsAssigned",
                "incident.inCategory",
                "category.kbArticlesInCategory",
            ],
        ),
        ResolveOp(id="out", source="$kb", fields=["number", "short_description"]),
    )
    out = validator.validate(plan)
    assert isinstance(out.operations[1], TraverseOp)
    assert out.operations[1].path[0] == "sys_user.incidentsAssigned"


def test_declarative_traverse_rejects_unknown_to_entity(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="x",
            **{"from": "$u"},
            to_entity="does_not_exist",
            path=["sys_user.incidentsReported"],
        ),
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("to_entity" in e for e in exc.value.errors)


def test_declarative_traverse_accepts_non_shortest_chain_within_cap(
    validator: PlanValidator,
) -> None:
    """Non-shortest chains are now ACCEPTED — the reviewer asked us to
    rank candidate chains by user phrasing, which requires allowing
    longer chains alongside shorter ones. The PDF's "Ravi's team"
    query needs a 2-hop chain to sys_user_group even though the 1-hop
    `managesGroups` chain exists. Tests of the old rejection are gone."""
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="x",
            **{"from": "$u"},
            to_entity="incident",
            path=[
                "sys_user.incidentsAssigned",
                "incident.handledBy",
                "sys_user_group.incidentsHandled",
            ],
        ),
        ResolveOp(id="out", source="$x", fields=["number"]),
    )
    out = validator.validate(plan)
    assert out is not None  # no PlanValidationError raised


def test_declarative_traverse_rejects_invented_relation(
    validator: PlanValidator,
) -> None:
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="x",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.notARealRelation"],
        ),
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("not in schema" in e for e in exc.value.errors)


def test_declarative_traverse_rejects_filters_by_entity_off_chain(
    validator: PlanValidator,
) -> None:
    """filters_by_entity keys must be entities the chain actually visits.
    Filter on sys_user_group in a sys_user -> incident chain has nowhere
    to apply."""
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="x",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.incidentsReported"],
            filters_by_entity={
                "sys_user_group": [
                    Filter(field="name", operator="eq", value="anything")
                ],
            },
        ),
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    assert any("not in the resolved chain" in e for e in exc.value.errors)


def test_declarative_traverse_translates_per_entity_filter_value_maps(
    validator: PlanValidator,
) -> None:
    """The validator transforms display-string filter values to value-map
    codes inside filters_by_entity, per entity."""
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="inc",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.incidentsReported"],
            filters_by_entity={
                "incident": [
                    Filter(
                        field="state",
                        operator="in",
                        value=["New", "In Progress", "On Hold"],
                    ),
                ],
            },
        ),
        ResolveOp(id="out", source="$inc", fields=["number"]),
    )
    out = validator.validate(plan)
    transformed = out.operations[1]
    assert isinstance(transformed, TraverseOp)
    assert transformed.filters_by_entity["incident"][0].value == [1, 2, 3]


def test_declarative_traverse_rejects_when_path_exceeds_max_hops(
    validator: PlanValidator,
) -> None:
    """validate_path enforces shortest; the planner cannot smuggle a
    long chain in via MAX_PATH_HOPS. Construct a long-but-valid chain
    that exceeds the cap by alternating hops."""
    # Build a 5-hop chain that exceeds MAX_PATH_HOPS=4 but is otherwise
    # syntactically valid: sys_user -> incident -> sys_user -> incident
    # -> sys_user -> incident.
    plan = _plan(
        FindOp(id="u", entity="sys_user"),
        TraverseOp(
            id="x",
            **{"from": "$u"},
            to_entity="incident",
            path=[
                "sys_user.incidentsReported",
                "incident.reportedBy",
                "sys_user.incidentsAssigned",
                "incident.assignedTo",
                "sys_user.incidentsReported",
            ],
        ),
    )
    with pytest.raises(PlanValidationError) as exc:
        validator.validate(plan)
    msgs = exc.value.errors
    assert any("MAX_PATH_HOPS" in e or "not shortest" in e for e in msgs)
