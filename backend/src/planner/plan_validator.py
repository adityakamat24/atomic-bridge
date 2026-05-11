from __future__ import annotations

from typing import Any

from src.core.data_store import Filter
from src.core.schema_graph import SchemaGraph, ValueMap
from src.planner.plan_schema import (
    AggregateOp,
    FindOp,
    KBLookupOp,
    Operation,
    QueryPlan,
    ResolveOp,
    TraverseOp,
    WriteProposalOp,
)

MAX_OPERATIONS = 10
MAX_TRAVERSE_OPS = 5
MAX_FIND_LIMIT = 1000

WRITE_INCIDENT_ALLOWED_FIELDS = {
    "short_description",
    "description",
    "state",
    "priority",
    "category",
    "caller_id",
    "assigned_to",
    "assignment_group",
}


class PlanValidationError(Exception):
    """Raised when a plan fails any validator check.

    The `errors` attribute is a list of human-readable strings; the API layer
    surfaces them verbatim to the user, NOT as a 'let me retry' loop, since
    silent retries hide injection attempts and waste tokens (03-planner.md).
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class PlanValidator:
    """Deterministic, pure-Python validator. No LLM calls.

    Returns a (possibly transformed) plan: filter values for value-mapped
    fields are converted display->code, and PII fields are stripped from
    `resolve.fields` unless the session has `allow_pii=True`.
    """

    def __init__(self, graph: SchemaGraph, allow_pii: bool = False) -> None:
        self._graph = graph
        self._allow_pii = allow_pii

    # ------------------------------------------------------------------ API

    def validate(self, plan: QueryPlan) -> QueryPlan:
        errors: list[str] = []

        if plan.intent in ("ambiguous", "out_of_scope"):
            return plan  # already vetted by Pydantic model_validator

        # Resource limits
        if len(plan.operations) > MAX_OPERATIONS:
            errors.append(
                f"too many operations: {len(plan.operations)} > {MAX_OPERATIONS}"
            )
        traverse_count = sum(1 for op in plan.operations if isinstance(op, TraverseOp))
        if traverse_count > MAX_TRAVERSE_OPS:
            errors.append(
                f"too many traverse ops: {traverse_count} > {MAX_TRAVERSE_OPS}"
            )

        # write_proposal must be terminal
        for i, op in enumerate(plan.operations):
            if isinstance(op, WriteProposalOp) and i < len(plan.operations) - 1:
                errors.append(
                    f"op {op.id!r}: write_proposal must be the last operation"
                )

        # Per-op checks. Walk in order so $var refs can be checked.
        defined_vars: set[str] = set()
        for op in plan.operations:
            self._validate_op(op, defined_vars, errors)
            defined_vars.add(op.id)

        # Output spec must reference a defined variable.
        if plan.output_spec and plan.output_spec.final_var not in defined_vars:
            errors.append(
                f"output_spec.final_var {plan.output_spec.final_var!r} "
                f"is not defined by any operation"
            )

        if errors:
            raise PlanValidationError(errors)

        return self._transform(plan)

    # ------------------------------------------------------------- per-op

    def _validate_op(
        self, op: Operation, defined_vars: set[str], errors: list[str]
    ) -> None:
        if isinstance(op, FindOp):
            self._check_find(op, errors)
        elif isinstance(op, TraverseOp):
            self._check_traverse(op, defined_vars, errors)
        elif isinstance(op, AggregateOp):
            self._check_aggregate(op, defined_vars, errors)
        elif isinstance(op, KBLookupOp):
            pass  # KB has no graph-side schema constraints
        elif isinstance(op, ResolveOp):
            self._check_resolve(op, defined_vars, errors)
        elif isinstance(op, WriteProposalOp):
            self._check_write_proposal(op, defined_vars, errors)

    def _check_find(self, op: FindOp, errors: list[str]) -> None:
        if not self._graph.has_entity(op.entity):
            errors.append(f"op {op.id!r}: entity {op.entity!r} does not exist")
            return
        if op.limit > MAX_FIND_LIMIT:
            errors.append(
                f"op {op.id!r}: limit {op.limit} exceeds max {MAX_FIND_LIMIT}"
            )
        self._check_filters(op.filters, op.entity, op.id, errors)

    def _check_traverse(
        self, op: TraverseOp, defined_vars: set[str], errors: list[str]
    ) -> None:
        var_id = op.from_var.lstrip("$")
        if var_id not in defined_vars:
            errors.append(
                f"op {op.id!r}: from {op.from_var!r} references undefined variable"
            )
        if not self._graph.has_relation(op.relation):
            errors.append(
                f"op {op.id!r}: relation {op.relation!r} does not exist"
            )
            return
        rel = self._graph.relation(op.relation)
        self._check_filters(op.filters, rel.to_entity, op.id, errors)

    def _check_aggregate(
        self, op: AggregateOp, defined_vars: set[str], errors: list[str]
    ) -> None:
        var_id = op.source.lstrip("$")
        if var_id not in defined_vars:
            errors.append(
                f"op {op.id!r}: source {op.source!r} references undefined variable"
            )
        if op.operation in ("group_by_count", "top_n") and not op.group_by_field:
            errors.append(
                f"op {op.id!r}: {op.operation} requires group_by_field"
            )
        if op.operation == "top_n" and not op.n:
            errors.append(f"op {op.id!r}: top_n requires n")

    def _check_resolve(
        self, op: ResolveOp, defined_vars: set[str], errors: list[str]
    ) -> None:
        var_id = op.source.lstrip("$")
        if var_id not in defined_vars:
            errors.append(
                f"op {op.id!r}: source {op.source!r} references undefined variable"
            )
        # Field-existence check is best-effort: we don't know the source
        # entity from the variable alone (would need an executor-style
        # type-tracking pass). Phase 7 verifies at execution time.

    def _check_write_proposal(
        self, op: WriteProposalOp, defined_vars: set[str], errors: list[str]
    ) -> None:
        if op.target_var:
            var_id = op.target_var.lstrip("$")
            if var_id not in defined_vars:
                errors.append(
                    f"op {op.id!r}: target_var {op.target_var!r} undefined"
                )
        if op.action == "update_incident" and not op.target_var:
            errors.append(f"op {op.id!r}: update_incident requires target_var")
        bad = set(op.fields) - WRITE_INCIDENT_ALLOWED_FIELDS
        if bad:
            errors.append(
                f"op {op.id!r}: write_proposal has disallowed fields: {sorted(bad)}"
            )

    # ----------------------------------------------------------- filters

    def _check_filters(
        self,
        filters: list[Filter],
        entity_id: str,
        op_id: str,
        errors: list[str],
    ) -> None:
        for f in filters:
            field_id = f.field if "." in f.field else f"{entity_id}.{f.field}"
            if not self._graph.has_field(field_id):
                errors.append(
                    f"op {op_id!r}: field {f.field!r} does not exist on "
                    f"{entity_id!r}"
                )
                continue
            field = self._graph.field(field_id)
            if field.value_map_id is None or f.value is None:
                continue
            vm = self._graph.value_map(field.value_map_id)
            self._check_value_map_value(f.value, vm, op_id, f.field, errors)

    def _check_value_map_value(
        self,
        value: Any,
        vm: ValueMap,
        op_id: str,
        field: str,
        errors: list[str],
    ) -> None:
        check = value if isinstance(value, list) else [value]
        for v in check:
            if isinstance(v, int):
                if v not in vm.forward:
                    errors.append(
                        f"op {op_id!r}: filter on {field!r}: code {v} not "
                        f"in value_map {vm.id!r}"
                    )
            elif (
                isinstance(v, str)
                and v not in vm.forward.values()
                and vm.fuzzy_from_display(v) is None
            ):
                errors.append(
                    f"op {op_id!r}: filter on {field!r}: {v!r} not in "
                    f"value_map {vm.id!r} display values"
                )

    # --------------------------------------------------------- transform

    def _transform(self, plan: QueryPlan) -> QueryPlan:
        """Apply two transforms:
        - Convert display strings to integer codes for value-mapped filter values.
        - Strip is_sensitive fields from resolve.fields unless allow_pii.
        """
        new_ops: list[Operation] = []
        for op in plan.operations:
            if isinstance(op, FindOp):
                new_ops.append(
                    op.model_copy(
                        update={
                            "filters": [
                                self._translate_filter(f, op.entity)
                                for f in op.filters
                            ]
                        }
                    )
                )
            elif isinstance(op, TraverseOp):
                rel = self._graph.relation(op.relation)
                new_ops.append(
                    op.model_copy(
                        update={
                            "filters": [
                                self._translate_filter(f, rel.to_entity)
                                for f in op.filters
                            ]
                        }
                    )
                )
            elif isinstance(op, ResolveOp):
                new_ops.append(self._strip_pii_fields(op))
            else:
                new_ops.append(op)

        return plan.model_copy(update={"operations": new_ops})

    def _translate_filter(self, f: Filter, entity_id: str) -> Filter:
        field_id = f.field if "." in f.field else f"{entity_id}.{f.field}"
        if not self._graph.has_field(field_id):
            return f
        field = self._graph.field(field_id)
        if field.value_map_id is None or f.value is None:
            return f
        vm = self._graph.value_map(field.value_map_id)

        def to_code(v: Any) -> Any:
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                fuzzy = vm.fuzzy_from_display(v)
                if fuzzy is not None:
                    return fuzzy
                try:
                    return vm.from_display(v)
                except KeyError:
                    return v
            return v

        new_value = (
            [to_code(v) for v in f.value]
            if isinstance(f.value, list)
            else to_code(f.value)
        )
        return f.model_copy(update={"value": new_value})

    def _strip_pii_fields(self, op: ResolveOp) -> ResolveOp:
        if self._allow_pii:
            return op
        # The resolve op doesn't carry the source entity — we strip
        # conservatively by checking each field name against ALL is_sensitive
        # field names in the graph. False positives are acceptable because the
        # planner is supposed to use entity-qualified field IDs already
        # (see 03-planner.md).
        sensitive_names = {
            f.name
            for entity in self._graph.all_entities()
            for f in self._graph.fields_of(entity.id)
            if f.is_sensitive
        }
        clean = [
            name for name in op.fields if name.split(".")[-1] not in sensitive_names
        ]
        if clean == op.fields:
            return op
        return op.model_copy(update={"fields": clean})
