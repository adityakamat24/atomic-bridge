from __future__ import annotations

import re
from collections import deque
from typing import Any, Literal

import networkx as nx
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Edge type constants. Used as the `type` attribute on every NetworkX edge.
# ---------------------------------------------------------------------------

EDGE_HAS_FIELD = "HAS_FIELD"
EDGE_MAPS_THROUGH = "MAPS_THROUGH"
EDGE_VIA = "VIA"
EDGE_FROM = "FROM"
EDGE_TO = "TO"
EDGE_INVERSE_OF = "INVERSE_OF"


DataType = Literal["string", "integer", "datetime", "reference", "boolean"]
Cardinality = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]


# ---------------------------------------------------------------------------
# Node-type Pydantic models. These are the four first-class node types
# described in 02-schema-graph.md.
# ---------------------------------------------------------------------------


class Entity(BaseModel):
    id: str
    display_name: str
    description: str = ""
    primary_key: str = "sys_id"
    table_name: str
    data_path: str


class FieldNode(BaseModel):
    """Renamed from `Field` to avoid collision with `pydantic.Field`."""

    id: str
    entity_id: str
    name: str
    display_name: str
    description: str = ""
    data_type: DataType
    references: str | None = None
    value_map_id: str | None = None
    is_sensitive: bool = False
    is_searchable: bool = True


class ValueMap(BaseModel):
    id: str
    description: str = ""
    forward: dict[int, str]

    def to_display(self, code: int) -> str:
        try:
            return self.forward[code]
        except KeyError as exc:
            raise KeyError(f"value_map {self.id!r}: no display for code {code!r}") from exc

    def from_display(self, label: str) -> int:
        for code, display in self.forward.items():
            if display == label:
                return code
        raise KeyError(f"value_map {self.id!r}: no code for display {label!r}")

    def fuzzy_from_display(self, label: str) -> int | None:
        """Match 'in progress', 'In-Progress', 'in_progress' -> the same code.

        Normalisation is intentionally narrow: lower-case + strip non-alphanumeric.
        Anything beyond that (synonyms like "progressing") needs an LLM.
        """
        target = _normalize(label)
        for code, display in self.forward.items():
            if _normalize(display) == target:
                return code
        return None


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Relation(BaseModel):
    id: str
    verb_phrase: str
    inverse_verb_phrase: str = ""
    from_entity: str
    to_entity: str
    via_field: str
    cardinality: Cardinality
    inverse_relation_id: str | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# SchemaGraph
# ---------------------------------------------------------------------------


class SchemaGraph:
    """Backed by a NetworkX MultiDiGraph.

    Insertion order is preserved on the four lookup dicts so that
    `to_llm_context()` is deterministic without alphabetical reshuffling.
    """

    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._entities: dict[str, Entity] = {}
        self._fields: dict[str, FieldNode] = {}
        self._value_maps: dict[str, ValueMap] = {}
        self._relations: dict[str, Relation] = {}

    # ----- builders (called by the loader) --------------------------------

    def add_value_map(self, vm: ValueMap) -> None:
        self._value_maps[vm.id] = vm
        self._g.add_node(vm.id, kind="ValueMap", obj=vm)

    def add_entity(self, entity: Entity) -> None:
        self._entities[entity.id] = entity
        self._g.add_node(entity.id, kind="Entity", obj=entity)

    def add_field(self, field: FieldNode) -> None:
        self._fields[field.id] = field
        self._g.add_node(field.id, kind="Field", obj=field)
        # Entity --HAS_FIELD--> Field
        self._g.add_edge(field.entity_id, field.id, type=EDGE_HAS_FIELD)
        if field.value_map_id is not None:
            # Field --MAPS_THROUGH--> ValueMap
            self._g.add_edge(field.id, field.value_map_id, type=EDGE_MAPS_THROUGH)

    def add_relation(self, relation: Relation) -> None:
        self._relations[relation.id] = relation
        self._g.add_node(relation.id, kind="Relation", obj=relation)
        # Relation --FROM--> Entity (origin)
        self._g.add_edge(relation.id, relation.from_entity, type=EDGE_FROM)
        # Relation --TO--> Entity (target)
        self._g.add_edge(relation.id, relation.to_entity, type=EDGE_TO)
        # Relation --VIA--> Field (implementation)
        self._g.add_edge(relation.id, relation.via_field, type=EDGE_VIA)

    def link_inverses(self) -> None:
        """Add INVERSE_OF edges. Called once after all relations are loaded."""
        for rel in self._relations.values():
            inv = rel.inverse_relation_id
            if inv and inv in self._relations:
                self._g.add_edge(rel.id, inv, type=EDGE_INVERSE_OF)

    # ----- lookups --------------------------------------------------------

    def entity(self, entity_id: str) -> Entity:
        return self._entities[entity_id]

    def field(self, field_id: str) -> FieldNode:
        return self._fields[field_id]

    def relation(self, relation_id: str) -> Relation:
        return self._relations[relation_id]

    def value_map(self, value_map_id: str) -> ValueMap:
        return self._value_maps[value_map_id]

    def has_entity(self, entity_id: str) -> bool:
        return entity_id in self._entities

    def has_field(self, field_id: str) -> bool:
        return field_id in self._fields

    def has_relation(self, relation_id: str) -> bool:
        return relation_id in self._relations

    # ----- traversal ------------------------------------------------------

    def fields_of(self, entity_id: str) -> list[FieldNode]:
        return [f for f in self._fields.values() if f.entity_id == entity_id]

    def relations_from(self, entity_id: str) -> list[Relation]:
        return [r for r in self._relations.values() if r.from_entity == entity_id]

    def relations_to(self, entity_id: str) -> list[Relation]:
        return [r for r in self._relations.values() if r.to_entity == entity_id]

    def expand_subgraph(
        self, seed_ids: list[str], max_hops: int = 2
    ) -> list[str]:
        """BFS over the relation graph starting from `seed_ids`. Returns every
        entity reachable within `max_hops` (inclusive), in BFS order, with seeds
        first. Used to compensate for an imperfect subgraph hint from the
        preprocessor: if it returns `[sys_user]` but the query needs to traverse
        to `incident`, the planner still needs `incident` in its context.

        Both directions of the relation graph count as one hop — production
        graphs frequently have asymmetric relation pairs (only the outbound
        side defined), so following inbound edges is necessary for the filter
        to be safe at scale.
        """
        out: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for sid in seed_ids:
            if sid in self._entities and sid not in out:
                out[sid] = 0
                queue.append((sid, 0))
        while queue:
            eid, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for rel in self.relations_from(eid):
                if rel.to_entity not in out:
                    out[rel.to_entity] = depth + 1
                    queue.append((rel.to_entity, depth + 1))
            for rel in self.relations_to(eid):
                if rel.from_entity not in out:
                    out[rel.from_entity] = depth + 1
                    queue.append((rel.from_entity, depth + 1))
        return list(out.keys())

    def shortest_relation_path(
        self, from_entity: str, to_entity: str
    ) -> list[Relation] | None:
        """BFS over entities, with relations as the edges. Returns the
        list of Relations forming the path, or None if unreachable.
        Returns [] if from == to.
        """
        if from_entity not in self._entities or to_entity not in self._entities:
            return None
        if from_entity == to_entity:
            return []
        visited: set[str] = {from_entity}
        queue: deque[tuple[str, list[Relation]]] = deque([(from_entity, [])])
        while queue:
            current, path = queue.popleft()
            for rel in self.relations_from(current):
                if rel.to_entity == to_entity:
                    return [*path, rel]
                if rel.to_entity not in visited:
                    visited.add(rel.to_entity)
                    queue.append((rel.to_entity, [*path, rel]))
        return None

    # ----- introspection --------------------------------------------------

    def all_entities(self) -> list[Entity]:
        return list(self._entities.values())

    def all_relations(self) -> list[Relation]:
        return list(self._relations.values())

    def all_value_maps(self) -> list[ValueMap]:
        return list(self._value_maps.values())

    def counts(self) -> dict[str, int]:
        return {
            "entities": len(self._entities),
            "fields": len(self._fields),
            "value_maps": len(self._value_maps),
            "relations": len(self._relations),
        }

    # ----- LLM-facing serialization --------------------------------------

    def to_llm_context(self, entity_ids: list[str] | None = None) -> str:
        """Markdown blob the planner sees. Deterministic by insertion order."""
        if entity_ids is None:
            entities = self.all_entities()
        else:
            entities = [self._entities[eid] for eid in entity_ids if eid in self._entities]

        lines: list[str] = ["# Available entities", ""]
        for entity in entities:
            lines.append(f"## {entity.id} ({entity.display_name})")
            if entity.description:
                lines.append(entity.description)
            lines.append("")
            lines.append("Fields:")
            for field in self.fields_of(entity.id):
                lines.append(self._render_field_line(field))
            lines.append("")
            outbound = self.relations_from(entity.id)
            if outbound:
                lines.append("Outbound relations:")
                for rel in outbound:
                    short_id = rel.id.split(".", 1)[1] if "." in rel.id else rel.id
                    lines.append(
                        f"- {short_id}: {rel.from_entity} {rel.verb_phrase} {rel.to_entity} "
                        f"(cardinality {rel.cardinality})"
                    )
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _render_field_line(self, field: FieldNode) -> str:
        type_str: str = field.data_type
        if field.value_map_id is not None:
            vm = self._value_maps.get(field.value_map_id)
            if vm is not None:
                pairs = ", ".join(f"{c}={d}" for c, d in vm.forward.items())
                type_str = f"integer with value map [{pairs}]"
        elif field.data_type == "reference" and field.references:
            type_str = f"reference -> {field.references}"

        suffix = ""
        if field.description:
            suffix = f": {field.description}"
        sensitive = " [SENSITIVE]" if field.is_sensitive else ""
        return f"- {field.name} ({field.display_name}, {type_str}){suffix}{sensitive}"

    def to_visjs(self, highlight_path: list[str] | None = None) -> dict[str, Any]:
        """Returns a vis-network compatible {nodes, edges} dict for the schema explorer."""
        highlight = set(highlight_path or [])
        nodes: list[dict[str, Any]] = []
        for entity in self._entities.values():
            nodes.append(
                {
                    "id": entity.id,
                    "label": entity.display_name,
                    "group": "entity",
                    "title": entity.description,
                }
            )
        for vm in self._value_maps.values():
            nodes.append(
                {
                    "id": vm.id,
                    "label": vm.id,
                    "group": "value_map",
                    "title": vm.description,
                }
            )
        edges: list[dict[str, Any]] = []
        for rel in self._relations.values():
            edges.append(
                {
                    "id": rel.id,
                    "from": rel.from_entity,
                    "to": rel.to_entity,
                    "label": rel.verb_phrase,
                    "title": rel.description,
                    "highlighted": rel.id in highlight,
                    "cardinality": rel.cardinality,
                }
            )
        # Project Field --MAPS_THROUGH--> ValueMap edges as Entity --uses--> ValueMap
        # so the value-map nodes don't float disconnected in the visualization.
        # One edge per (entity, field-using-the-map) so a value map shared across
        # multiple fields/entities visibly fans out from each source.
        for field in self._fields.values():
            if not field.value_map_id:
                continue
            edges.append(
                {
                    "id": f"valuemap:{field.id}",
                    "from": field.entity_id,
                    "to": field.value_map_id,
                    "label": f"{field.name} maps to",
                    "title": f"{field.entity_id}.{field.name} -> {field.value_map_id}",
                    "highlighted": False,
                    "cardinality": "value_map",
                }
            )
        return {"nodes": nodes, "edges": edges}

    # ----- diagnostics ---------------------------------------------------

    def integrity_issues(self) -> list[str]:
        """Returns a list of human-readable warnings about the loaded graph.
        Empty list = clean. Used by tests and by /v1/schema introspection.
        """
        issues: list[str] = []
        for field in self._fields.values():
            if field.entity_id not in self._entities:
                issues.append(f"field {field.id}: entity {field.entity_id} missing")
            if field.references and field.references not in self._entities:
                issues.append(
                    f"field {field.id}: references {field.references} which is not an entity"
                )
            if field.value_map_id and field.value_map_id not in self._value_maps:
                issues.append(
                    f"field {field.id}: value_map {field.value_map_id} missing"
                )
        for rel in self._relations.values():
            if rel.from_entity not in self._entities:
                issues.append(f"relation {rel.id}: from_entity {rel.from_entity} missing")
            if rel.to_entity not in self._entities:
                issues.append(f"relation {rel.id}: to_entity {rel.to_entity} missing")
            if rel.via_field not in self._fields:
                issues.append(f"relation {rel.id}: via_field {rel.via_field} missing")
            if rel.inverse_relation_id and rel.inverse_relation_id not in self._relations:
                issues.append(
                    f"relation {rel.id}: inverse_relation_id "
                    f"{rel.inverse_relation_id} missing"
                )
        return issues
