from __future__ import annotations

from typing import Any

from src.core.schema_graph import SchemaGraph


def apply_value_maps(record: dict[str, Any], entity_id: str, graph: SchemaGraph) -> dict[str, Any]:
    """Translate value-mapped integer fields on a single record to their display strings.

    Pure function. Returns a new dict; does not mutate input. Reference fields
    are left untouched (resolution is the executor's job, see
    `executor/reference_resolver.py`).
    """
    out: dict[str, Any] = {}
    for key, value in record.items():
        field_id = f"{entity_id}.{key}"
        field = graph._fields.get(field_id)
        if field is not None and field.value_map_id is not None and isinstance(value, int):
            vm = graph.value_map(field.value_map_id)
            display = vm.forward.get(value)
            out[key] = display if display is not None else value
        else:
            out[key] = value
    return out


def display_to_code(value: str | int, field_id: str, graph: SchemaGraph) -> int | str:
    """Convert a display string back to its integer code if the field is value-mapped.

    If the value is already a code (int) it is returned unchanged. If the field
    has no value map the value passes through. Raises KeyError if the value map
    rejects the display string (no fuzzy match).
    """
    if isinstance(value, int):
        return value
    field = graph.field(field_id)
    if field.value_map_id is None:
        return value
    vm = graph.value_map(field.value_map_id)
    fuzzy = vm.fuzzy_from_display(value)
    if fuzzy is not None:
        return fuzzy
    return vm.from_display(value)
