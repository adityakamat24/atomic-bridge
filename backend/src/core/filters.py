"""Filter evaluation, factored out so both the in-memory store and the
graph's execution layer can apply filters without a circular import.

The store needs it for the indexed-find path (filtering records of an
entity). The graph needs it for per-hop filtering inside `walk` (applying
filters_by_entity at each entity in the resolved chain). Both depend on
the SchemaGraph for value-map translation, so the function takes graph
as a parameter and stays pure.

Display strings are accepted for value-mapped fields ("In Progress" as
well as the integer code 2) — the function translates display -> code at
match time. The validator is the strict gate; this layer stays permissive
so a direct API caller passing a display value still gets the right rows.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.data_store import Filter, Record

if TYPE_CHECKING:
    from src.core.schema_graph import SchemaGraph


def apply_filters(
    records: list[Record],
    filters: list[Filter],
    entity_id: str,
    graph: SchemaGraph,
) -> list[Record]:
    """Return records that match ALL filters (AND semantics)."""
    out = list(records)
    for f in filters:
        out = [r for r in out if _matches(r, f, entity_id, graph)]
    return out


def _matches(
    record: Record, f: Filter, entity_id: str, graph: SchemaGraph,
) -> bool:
    field_name = f.field.split(".")[-1]
    full_field_id = f.field if "." in f.field else f"{entity_id}.{f.field}"
    rv = record.get(field_name)

    op = f.operator
    if op == "is_null":
        return rv is None
    if op == "is_not_null":
        return rv is not None

    # All remaining operators need a non-null record value.
    if rv is None:
        return False

    value = _translate_value(f.value, full_field_id, graph)

    if op == "in":
        if not isinstance(value, list):
            return False
        return rv in value

    rv_cmp: Any = rv
    value_cmp: Any = value
    if (
        isinstance(rv, str)
        and isinstance(value, str)
        and not f.case_sensitive
    ):
        rv_cmp = rv.lower()
        value_cmp = value.lower()

    if op == "eq":
        return bool(rv_cmp == value_cmp)
    if op == "neq":
        return bool(rv_cmp != value_cmp)
    if op == "contains":
        if isinstance(rv_cmp, str) and isinstance(value_cmp, str):
            return value_cmp in rv_cmp
        if isinstance(rv, list):
            return value in rv
        return False
    if op == "gt":
        return bool(rv > value)
    if op == "lt":
        return bool(rv < value)
    if op == "gte":
        return bool(rv >= value)
    if op == "lte":
        return bool(rv <= value)
    return False


def _translate_value(
    value: Any, field_id: str, graph: SchemaGraph,
) -> Any:
    """Translate display strings to integer codes for value-mapped fields.
    Pass-through for unmapped fields, integer codes, None, and unknown labels.
    """
    if value is None:
        return value
    if not graph.has_field(field_id):
        return value
    field = graph.field(field_id)
    if field.value_map_id is None:
        return value
    vm = graph.value_map(field.value_map_id)

    def translate_one(v: Any) -> Any:
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

    if isinstance(value, list):
        return [translate_one(v) for v in value]
    return translate_one(value)
