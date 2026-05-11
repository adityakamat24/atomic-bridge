from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.core.schema_graph import (
    Entity,
    FieldNode,
    Relation,
    SchemaGraph,
    ValueMap,
)


def load(path: Path | str) -> SchemaGraph:
    """Load a SchemaGraph from a YAML schema file.

    The YAML format is described in 02-schema-graph.md. The loader is
    intentionally permissive: integrity issues (dangling references,
    missing inverses) are surfaced via SchemaGraph.integrity_issues()
    rather than raised here, so that an in-progress schema can still
    load and be inspected.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"schema file not found: {p}")
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return _build(raw)


def loads(text: str) -> SchemaGraph:
    """Same as load() but takes the YAML text directly. Used in tests."""
    raw: dict[str, Any] = yaml.safe_load(text) or {}
    return _build(raw)


def _build(raw: dict[str, Any]) -> SchemaGraph:
    graph = SchemaGraph()

    for vm_data in raw.get("value_maps", []) or []:
        graph.add_value_map(_parse_value_map(vm_data))

    for entity_data in raw.get("entities", []) or []:
        entity = _parse_entity(entity_data)
        graph.add_entity(entity)
        for field_data in entity_data.get("fields", []) or []:
            graph.add_field(_parse_field(field_data, entity.id))

    for rel_data in raw.get("relations", []) or []:
        graph.add_relation(_parse_relation(rel_data))

    graph.link_inverses()
    return graph


def _parse_value_map(data: dict[str, Any]) -> ValueMap:
    forward = {int(k): str(v) for k, v in (data.get("forward") or {}).items()}
    return ValueMap(
        id=str(data["id"]),
        description=str(data.get("description", "")),
        forward=forward,
    )


def _parse_entity(data: dict[str, Any]) -> Entity:
    return Entity(
        id=str(data["id"]),
        display_name=str(data.get("display_name", data["id"])),
        description=str(data.get("description", "")),
        primary_key=str(data.get("primary_key", "sys_id")),
        table_name=str(data.get("table_name", data["id"])),
        data_path=str(data.get("data_path", f"{data['id']}.json")),
    )


def _parse_field(data: dict[str, Any], entity_id: str) -> FieldNode:
    name = str(data["name"])
    return FieldNode(
        id=f"{entity_id}.{name}",
        entity_id=entity_id,
        name=name,
        display_name=str(data.get("display_name", name)),
        description=str(data.get("description", "")),
        data_type=str(data["data_type"]),  # type: ignore[arg-type]
        references=(str(data["references"]) if data.get("references") else None),
        value_map_id=(str(data["value_map"]) if data.get("value_map") else None),
        is_sensitive=bool(data.get("is_sensitive", False)),
        is_searchable=bool(data.get("is_searchable", True)),
    )


def _parse_relation(data: dict[str, Any]) -> Relation:
    return Relation(
        id=str(data["id"]),
        verb_phrase=str(data.get("verb_phrase", "")),
        inverse_verb_phrase=str(data.get("inverse_verb_phrase", "")),
        from_entity=str(data["from_entity"]),
        to_entity=str(data["to_entity"]),
        via_field=str(data["via_field"]),
        cardinality=str(data["cardinality"]),  # type: ignore[arg-type]
        inverse_relation_id=(
            str(data["inverse_relation_id"]) if data.get("inverse_relation_id") else None
        ),
        description=str(data.get("description", "")),
    )
