from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any, Literal

import networkx as nx
from pydantic import BaseModel

from src.core.data_store import Filter, Record
from src.core.filters import apply_filters as filter_records
from src.core.trace import ExecutionError, ExecutionResult, ExecutionTrace, TraceStep

if TYPE_CHECKING:
    from src.core.data_store import DataStore
    from src.knowledge.retriever import KBRetriever
    from src.planner.plan_schema import (
        AggregateOp,
        FindOp,
        KBLookupOp,
        QueryPlan,
        ResolveOp,
        TraverseOp,
        WriteProposalOp,
    )
    from src.planner.relation_scorer import RelationScorer, ScorerChoice
    from src.write_path.proposal import WriteProposal


# ---------------------------------------------------------------------------
# Result types for execution methods. Dataclasses (not Pydantic) for cheap
# construction inside hot paths; the trace serialization happens once at
# the API boundary via Pydantic models in executor/trace.py.
# ---------------------------------------------------------------------------


WriteAction = Literal["create_incident", "update_incident"]


@dataclass
class WalkResult:
    """Output of SchemaGraph.walk — the final hop's records plus the chain
    of relation IDs that were actually walked and the entity hops where
    filters_by_entity fired.

    ``scorer_choice`` is populated only when the engine had to rank
    multiple candidate chains (the reviewer's LLM-driven scoring pass).

    ``attempted_paths`` records every chain the engine tried, in
    ranking order — each entry has ``path``, ``records_count``, and
    ``used`` (the one that yielded the result). Empty when there was
    only one candidate or the planner supplied an explicit path.
    """
    records: list[Record]
    chain: list[str]
    hops_filtered: list[str] = dc_field(default_factory=list)
    scorer_choice: ScorerChoice | None = None
    attempted_paths: list[dict[str, Any]] = dc_field(default_factory=list)


@dataclass
class ResolveOutcome:
    """Output of SchemaGraph.resolve. Carries per-call warnings so the
    plan walker can append them to the execution trace and the response
    generator can surface dangling references explicitly."""
    records: list[Record]
    relations_used: list[str] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)


AggOp = Literal["count", "count_distinct", "group_by_count", "top_n"]

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

# Maximum number of relation chains the engine will try before giving up
# on a single traverse op. The walk() method ranks candidate chains
# (planner's hint first, then enumerate_simple_paths order) and walks
# them until one returns records. Beyond this cap, the engine stops and
# returns empty even if more candidates exist — both because each walk
# costs a store call AND because the Nth-ranked chain is increasingly
# unlikely to be the user's actual intent.
MAX_FALLBACK_ATTEMPTS = 4


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

    def __init__(
        self,
        *,
        store: DataStore | None = None,
        kb: KBRetriever | None = None,
    ) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._entities: dict[str, Entity] = {}
        self._fields: dict[str, FieldNode] = {}
        self._value_maps: dict[str, ValueMap] = {}
        self._relations: dict[str, Relation] = {}
        # Runtime collaborators. Set via __init__ or bind_runtime(). Execution
        # methods (find / walk / aggregate / resolve / kb_search /
        # propose_write / execute_plan) require store to be set; they raise
        # RuntimeError otherwise. Metadata methods work without binding.
        self._store: DataStore | None = store
        self._kb: KBRetriever | None = kb

    def bind_runtime(
        self, store: DataStore, kb: KBRetriever | None = None,
    ) -> None:
        """Attach runtime collaborators after schema construction.

        Construction order in production is: load schema from YAML (no store
        yet), build store using schema metadata, build KB using store, then
        call bind_runtime to give the graph access to both. The schema-loader
        path can't supply them at __init__ time, so this method exists to
        complete the wiring without forcing the loader to know about the
        runtime layer.
        """
        self._store = store
        self._kb = kb

    @property
    def store(self) -> DataStore:
        if self._store is None:
            raise RuntimeError(
                "SchemaGraph has no DataStore bound. "
                "Call bind_runtime(store, kb) before using execution methods."
            )
        return self._store

    @property
    def kb(self) -> KBRetriever | None:
        return self._kb

    @property
    def is_runtime_bound(self) -> bool:
        return self._store is not None

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

        Kept for back-compat and debugger usage; the planner / validator
        use shortest_relation_paths (plural) below.
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

    def _entity_projection(self) -> nx.DiGraph:
        """Project the relation set onto a simple directed entity graph
        (entity nodes, one edge per outbound relation direction). Used as
        the substrate for nx.all_shortest_paths / nx.shortest_path_length
        in shortest_relation_paths and validate_path.

        Note: parallel relations between the same entity pair (e.g.
        sys_user -> incident via both incidentsReported and incidentsAssigned)
        collapse to a single edge in the projection. shortest_relation_paths
        enumerates the parallel relations per hop after the projection
        returns its entity sequences.
        """
        proj: nx.DiGraph = nx.DiGraph()
        for eid in self._entities:
            proj.add_node(eid)
        for rel in self._relations.values():
            proj.add_edge(rel.from_entity, rel.to_entity)
        return proj

    def shortest_relation_paths(
        self, from_entity: str, to_entity: str, max_hops: int = 4
    ) -> list[list[Relation]]:
        """Return every relation chain tied for SHORTEST hop count from
        from_entity to to_entity. Each chain is a list of Relations.

        - Returns [] if either entity is missing, the entities are
          unreachable, or the shortest path exceeds max_hops.
        - Returns [[]] when from_entity == to_entity (the trivial walk).
        - When parallel relations exist between the same entity pair on
          the shortest path (e.g. caller vs assignee on sys_user -> incident),
          every cartesian combination is enumerated. The planner picks one;
          the validator confirms the pick is valid via validate_path.

        Uses nx.all_shortest_paths on an entity-only projection. Bounded
        work: O(|V| + |E|) per BFS; cartesian fan-out is small in practice
        because parallel relations between the same pair are rare.
        """
        if from_entity not in self._entities or to_entity not in self._entities:
            return []
        if from_entity == to_entity:
            return [[]]

        proj = self._entity_projection()
        if not nx.has_path(proj, from_entity, to_entity):
            return []
        shortest_len = nx.shortest_path_length(proj, from_entity, to_entity)
        if shortest_len > max_hops:
            return []

        entity_paths = list(
            nx.all_shortest_paths(proj, from_entity, to_entity)
        )

        result: list[list[Relation]] = []
        for entity_path in entity_paths:
            # Build list-of-lists: parallel relation choices per hop.
            rels_per_hop: list[list[Relation]] = []
            for src, dst in zip(entity_path[:-1], entity_path[1:], strict=True):
                hop_rels = [
                    r for r in self._relations.values()
                    if r.from_entity == src and r.to_entity == dst
                ]
                rels_per_hop.append(hop_rels)
            # Cartesian product of relation choices, preserving insertion order.
            stack: list[list[Relation]] = [[]]
            for choices in rels_per_hop:
                stack = [chain + [c] for chain in stack for c in choices]
            result.extend(stack)
        return result

    def enumerate_simple_paths(
        self,
        from_entity: str,
        to_entity: str,
        max_hops: int = 4,
    ) -> list[list[Relation]]:
        """Every simple relation chain from ``from_entity`` to ``to_entity``
        with hop count at most ``max_hops``, sorted shortest-first.

        Differs from ``shortest_relation_paths`` in two important ways:

        1. **Does NOT restrict to chains tied for shortest length.** Returns
           a 1-hop chain alongside a 2-hop and 3-hop chain between the same
           pair when those longer chains exist. The reviewer asked us to
           rank chains by user phrasing — that requires presenting MORE
           than just the shortest options. The PDF's example query "What
           open issues does Ravi Kumar's team currently have assigned to
           them?" requires walking a 3-hop chain (sys_user → incident →
           sys_user_group → incident) when the user is not a manager, and
           the 1-hop shortest (sys_user.managesGroups → sys_user_group)
           gives an empty result.

        2. **Sorts by length, then by insertion order** so callers can
           prefer shorter chains while still seeing longer alternatives.
           The relation scorer uses this order as a stability tiebreaker.

        Returns ``[[]]`` for the reflexive case (from == to). Returns
        ``[]`` when unreachable or either entity is unknown.
        """
        if from_entity not in self._entities or to_entity not in self._entities:
            return []

        proj = self._entity_projection()

        # Special case for from == to. `nx.all_simple_paths(src, src)`
        # only returns the trivial 0-length path [src] — it does NOT
        # find self-loop relations or cycles back to the source. So we
        # have to assemble those chains manually:
        #   1. The empty path (trivial no-op walk).
        #   2. Every 1-hop self-relation (e.g., `sys_user.managedBy` /
        #      `sys_user.manages` — same entity type, different records:
        #      Ravi.managedBy = Deepak).
        #   3. 2-hop chains via another entity and back (e.g.,
        #      sys_user -> incident -> sys_user via incidentsReported +
        #      reportedBy, finding the reporter of your own incidents).
        # Longer cycles get cut by max_hops anyway.
        if from_entity == to_entity:
            results: list[list[Relation]] = [[]]
            # 1-hop self-relations.
            for rel in self._relations.values():
                if rel.from_entity == from_entity and rel.to_entity == from_entity:
                    results.append([rel])
            # 2..max_hops chains via other entities. For each
            # neighbour, walk out and back.
            if max_hops >= 2:
                for neighbour in self._entities:
                    if neighbour == from_entity:
                        continue
                    out_rels = [
                        r for r in self._relations.values()
                        if r.from_entity == from_entity and r.to_entity == neighbour
                    ]
                    back_rels = [
                        r for r in self._relations.values()
                        if r.from_entity == neighbour and r.to_entity == from_entity
                    ]
                    for o in out_rels:
                        for b in back_rels:
                            results.append([o, b])
            results.sort(key=len)
            return results

        if not nx.has_path(proj, from_entity, to_entity):
            return []

        # Enumerate every entity-level simple path within max_hops, then
        # for each one enumerate the cartesian product of parallel
        # relations per hop. Bounded work: max_hops≤4 and 14 relations
        # keeps fan-out tiny.
        entity_paths = list(
            nx.all_simple_paths(proj, from_entity, to_entity, cutoff=max_hops)
        )

        results = []
        for entity_path in entity_paths:
            rels_per_hop: list[list[Relation]] = []
            for src, dst in zip(entity_path[:-1], entity_path[1:], strict=True):
                hop_rels = [
                    r for r in self._relations.values()
                    if r.from_entity == src and r.to_entity == dst
                ]
                rels_per_hop.append(hop_rels)
            stack: list[list[Relation]] = [[]]
            for choices in rels_per_hop:
                stack = [chain + [c] for chain in stack for c in choices]
            results.extend(stack)
        # Stable sort: shortest first, then in insertion order.
        results.sort(key=len)
        return results

    def validate_path(
        self,
        from_entity: str,
        to_entity: str,
        path: list[str],
        max_hops: int = 4,
    ) -> list[str]:
        """Return a list of human-readable errors describing why `path`
        is NOT a valid shortest relation chain from from_entity to
        to_entity. Empty list means the path is valid.

        Used by the plan validator to reject any planner-emitted chain
        that invents relations, breaks the chain, mismatches the target,
        is longer than the shortest, or exceeds max_hops.
        """
        errors: list[str] = []

        if from_entity not in self._entities:
            errors.append(f"from_entity {from_entity!r} not in schema")
        if to_entity not in self._entities:
            errors.append(f"to_entity {to_entity!r} not in schema")
        if errors:
            return errors

        # Empty path with from == to: trivial walk (return source as-is).
        # Empty path with from != to: invalid — can't walk to a different
        # entity without traversing relations.
        if not path:
            if from_entity != to_entity:
                errors.append(
                    f"empty path is invalid for walk "
                    f"{from_entity!r} -> {to_entity!r}"
                )
            return errors

        # Non-empty path: validate chain normally. This handles both
        # cross-entity walks (from != to) AND self-relation walks
        # (from == to via a self-loop relation like `sys_user.managedBy`
        # — same entity type, different records: Ravi -> Deepak). The
        # old "must be empty when from == to" gate was wrong because it
        # blocked the PDF's manager-of-X queries entirely.

        # Existence check first; bail before chain-connectivity since later
        # checks rely on relation lookups succeeding.
        rels: list[Relation] = []
        for rid in path:
            if not self.has_relation(rid):
                errors.append(f"relation {rid!r} not in schema")
                return errors
            rels.append(self.relation(rid))

        # Chain connects end-to-end.
        if rels[0].from_entity != from_entity:
            errors.append(
                f"path[0] {rels[0].id!r} starts at "
                f"{rels[0].from_entity!r}, expected {from_entity!r}"
            )
        if rels[-1].to_entity != to_entity:
            errors.append(
                f"path[-1] {rels[-1].id!r} ends at "
                f"{rels[-1].to_entity!r}, expected {to_entity!r}"
            )
        for i, (a, b) in enumerate(zip(rels[:-1], rels[1:], strict=True)):
            if a.to_entity != b.from_entity:
                errors.append(
                    f"chain break at index {i}: {a.id!r} ends at "
                    f"{a.to_entity!r} but {b.id!r} starts at {b.from_entity!r}"
                )

        if errors:
            return errors

        # Any simple chain within max_hops is acceptable. The reviewer's
        # prescription is to RANK candidate chains by user phrasing, not
        # to mechanically pick the shortest — and the PDF's "Ravi's
        # team" example requires a 2-hop chain that is NOT shortest
        # (the shortest sys_user -> sys_user_group is `managesGroups`,
        # which is empty for non-manager staff). We still cap at
        # max_hops to bound search; we still reject disconnected /
        # invented / mismatched chains above. The "must be shortest"
        # gate is removed deliberately.
        if len(path) > max_hops:
            errors.append(
                f"path length {len(path)} exceeds max_hops={max_hops}"
            )

        return errors

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

    # ----- execution methods --------------------------------------------
    # The graph is the executor. These methods call into the data store and
    # KB retriever (which are runtime collaborators bound via bind_runtime)
    # and return Python results that the plan walker (execute_plan, added
    # in step 6 of the redesign) composes into the final ExecutionResult.
    #
    # display_name_of caches per-call via the optional `cache` arg, which
    # callers initialise once per request and pass through. The cache is
    # explicit (not a member) so the graph stays stateless across requests.

    def display_name_of(
        self,
        entity_id: str,
        sys_id: str,
        cache: dict[tuple[str, str], Record | None] | None = None,
    ) -> str:
        """Resolve a sys_id reference to a human-readable display string.
        Returns '[Unknown <entity> <sys_id>]' for dangling references — the
        plan walker uses that placeholder to flag dangling-ref warnings.
        Per-request callers pass a `cache` dict to deduplicate lookups."""
        rec = self._lookup_record(entity_id, sys_id, cache)
        if rec is None:
            return f"[Unknown {entity_id} {sys_id}]"
        # Pick the field that best names this entity, falling back through
        # plausible alternatives before giving up. The mapping below mirrors
        # the legacy ReferenceResolver so existing tests / prose stay stable.
        if entity_id == "sys_user":
            return str(rec.get("name", f"User {sys_id}"))
        if entity_id == "sys_user_group":
            return str(rec.get("name", f"Group {sys_id}"))
        if entity_id == "incident":
            return str(rec.get("number", f"Incident {sys_id}"))
        if entity_id == "kb_knowledge":
            return str(rec.get("number", f"Article {sys_id}"))
        if entity_id == "category":
            return str(rec.get("name", sys_id))
        return f"[{entity_id} {sys_id}]"

    def _lookup_record(
        self,
        entity_id: str,
        sys_id: str,
        cache: dict[tuple[str, str], Record | None] | None,
    ) -> Record | None:
        key = (entity_id, sys_id)
        if cache is not None and key in cache:
            return cache[key]
        rec = self.store.get(entity_id, sys_id)
        if cache is not None:
            cache[key] = rec
        return rec

    def find(
        self,
        entity_id: str,
        filters: list[Filter] | None = None,
        limit: int = 100,
    ) -> list[Record]:
        """Indexed lookup via the bound DataStore. Plan-validator-side
        translation has already converted display strings to value-map
        codes; the store's filter evaluator handles the rest."""
        return self.store.find(entity_id, list(filters or []), limit=limit)

    async def walk(
        self,
        source_records: list[Record],
        source_entity: str,
        target_entity: str,
        path: list[str],
        *,
        filters_by_entity: dict[str, list[Filter]] | None = None,
        scorer: RelationScorer | None = None,
        user_query: str = "",
    ) -> WalkResult:
        """Walk a relation chain from source_records to target_entity.

        Two modes:
          1. **Explicit path** (``path`` is non-empty): walk it directly.
             The validator has already confirmed it's a valid shortest
             chain. No LLM involvement.
          2. **Engine-resolved path** (``path`` is empty AND source !=
             target): enumerate shortest chains via
             ``shortest_relation_paths``. If exactly one, walk it. If
             multiple, call ``scorer`` to pick — the reviewer's
             prescribed LLM-driven relation-scoring pass.

        Reflexive walk (source_entity == target_entity, path == []):
        returns source_records, optionally filtered by
        filters_by_entity[target_entity].

        ``filters_by_entity`` keys must be entities visited by the chain
        (or equal to source/target). Filters fire immediately after the
        walk reaches that entity. The validator catches off-chain keys
        for explicit paths; for engine-resolved paths, off-chain keys
        are silently skipped (the planner shouldn't generate them).

        Returns WalkResult(records, chain, hops_filtered, scorer_choice).
        ``chain`` is the actual relation ids walked. ``scorer_choice`` is
        populated only when the scorer was invoked.
        """
        fbe = filters_by_entity or {}
        scorer_choice: ScorerChoice | None = None
        attempted_paths: list[dict[str, Any]] = []

        # Reflexive: nothing to walk; possibly filter the source records.
        if not path and source_entity == target_entity:
            records = list(source_records)
            hops_filtered: list[str] = []
            target_filters = fbe.get(target_entity, [])
            if target_filters:
                records = filter_records(
                    records, target_filters, target_entity, self,
                )
                hops_filtered.append(target_entity)
            return WalkResult(
                records=records, chain=[], hops_filtered=hops_filtered,
                scorer_choice=None, attempted_paths=[],
            )

        # Build the ranked list of chains to try.
        #
        # The planner's explicit `path` is treated as a HINT — its first
        # guess — not an authoritative-and-only choice. The LLM often
        # picks the wrong chain when verb phrases superficially match
        # (e.g., `sys_user.managesGroups` has verb_phrase "manages team",
        # which matches "team" in the user's query even when the user
        # meant "the team a person works on"). Treating the planner's
        # pick as a starting attempt + falling back through other valid
        # chains on empty is the data-adaptive recovery: no role rules,
        # no schema-side hardcoding — just "if this chain returned no
        # data, try the next semantic interpretation."
        #
        # If `path` is empty, the engine enumerates from scratch.
        # If `path` is non-empty, it goes at rank 0 + the other simple
        # chains follow in scorer order (or insertion order without a
        # scorer).
        all_candidates = self.enumerate_simple_paths(
            source_entity, target_entity,
        )
        if not all_candidates:
            return WalkResult(
                records=[], chain=[], hops_filtered=[],
                scorer_choice=None, attempted_paths=[],
            )

        ranked_chains: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()

        def _add_chain(chain_ids: list[str]) -> None:
            key = tuple(chain_ids)
            if key not in seen:
                seen.add(key)
                ranked_chains.append(chain_ids)

        # Rank 0: planner's hint, if any.
        if path:
            _add_chain(list(path))

        # Remaining candidates ordering:
        # - When planner committed to an explicit path, we already have
        #   rank 0; fallbacks use deterministic insertion order
        #   (shortest-first). No scorer call needed because the planner
        #   already declared its preference.
        # - When planner emitted no path, ask the scorer to rank all
        #   candidates — this is the reviewer's "rank them by which one
        #   best matches the user's phrasing" prescription.
        if not path and len(all_candidates) > 1 and scorer is not None:
            scorer_choice = await scorer.choose(
                all_candidates,
                user_query=user_query,
                source_entity=source_entity,
                target_entity=target_entity,
            )
            order = list(scorer_choice.ranking) or list(range(len(all_candidates)))
        else:
            order = list(range(len(all_candidates)))

        for candidate_index in order:
            chain_ids = [r.id for r in all_candidates[candidate_index]]
            _add_chain(chain_ids)

        # Cap at MAX_FALLBACK_ATTEMPTS. Each store call is real cost,
        # and the Nth-ranked chain is increasingly unlikely to be the
        # user's intent. If every attempt within the cap is empty, we
        # surface "no records" with the planner's first preference in
        # the trace — better than silently walking 10 chains.
        if len(ranked_chains) > MAX_FALLBACK_ATTEMPTS:
            ranked_chains = ranked_chains[:MAX_FALLBACK_ATTEMPTS]

        # Walk down the ranked chains; first non-empty result wins.
        # We capture the FIRST attempt separately so that if every chain
        # returns empty, we surface the planner's first preference (its
        # chain + the hops where filters fired) in the trace, not the
        # last fallback. That keeps mid-hop filter tracking correct
        # ("filter DID apply at incident hop, even though final result
        # was empty") and makes the trace match the planner's intent.
        first_attempt: WalkResult | None = None
        for rank_index, chain_ids in enumerate(ranked_chains):
            attempt = self._walk_explicit_path(
                source_records, chain_ids, fbe,
                attempted_paths=[],
                scorer_choice=None,
            )
            if first_attempt is None:
                first_attempt = attempt
            attempted_paths.append({
                "rank": rank_index,
                "path": chain_ids,
                "records_count": len(attempt.records),
                "used": False,
            })
            if attempt.records:
                attempted_paths[-1]["used"] = True
                return WalkResult(
                    records=attempt.records,
                    chain=attempt.chain,
                    hops_filtered=attempt.hops_filtered,
                    scorer_choice=scorer_choice,
                    attempted_paths=attempted_paths,
                )

        # Every ranked chain returned zero. Surface the planner's first
        # preference (rank 0) as the "used" attempt + carry through its
        # chain and hops_filtered.
        if attempted_paths:
            attempted_paths[0]["used"] = True
        if first_attempt is None:
            return WalkResult(
                records=[], chain=[], hops_filtered=[],
                scorer_choice=scorer_choice, attempted_paths=attempted_paths,
            )
        return WalkResult(
            records=[],
            chain=list(first_attempt.chain),
            hops_filtered=list(first_attempt.hops_filtered),
            scorer_choice=scorer_choice,
            attempted_paths=attempted_paths,
        )

    def _walk_explicit_path(
        self,
        source_records: list[Record],
        chain_ids: list[str],
        filters_by_entity: dict[str, list[Filter]],
        *,
        attempted_paths: list[dict[str, Any]],
        scorer_choice: ScorerChoice | None,
    ) -> WalkResult:
        """Walk a specific relation chain end-to-end. Shared between the
        planner-supplied path branch and each fallback attempt in the
        engine-resolved branch."""
        current = list(source_records)
        chain: list[str] = []
        hops_filtered: list[str] = []
        for rel_id in chain_ids:
            rel = self.relation(rel_id)
            current = self._walk_one_relation(current, rel)
            chain.append(rel.id)
            entity_filters = filters_by_entity.get(rel.to_entity, [])
            if entity_filters:
                current = filter_records(
                    current, entity_filters, rel.to_entity, self,
                )
                hops_filtered.append(rel.to_entity)
        return WalkResult(
            records=current, chain=chain, hops_filtered=hops_filtered,
            scorer_choice=scorer_choice,
            attempted_paths=list(attempted_paths),
        )

    def _walk_one_relation(
        self, source_records: list[Record], rel: Relation,
    ) -> list[Record]:
        """Single-hop walk along `rel`. Cardinality-dispatched:

        - many_to_one / one_to_one: the source side carries the reference;
          read it off each source record and batch-fetch the targets via
          store.get_many. Dangling refs (sys_id not in the target table)
          are silently dropped here; the resolve step surfaces them as
          warnings via display_name_of's [Unknown ...] placeholder.

        - one_to_many / many_to_many: the target side carries the reference;
          collect the source primary keys and ask the store to find every
          target whose via-field references one of them. limit=1000 is
          intentionally high because the planner sets reasonable upper
          bounds; if a query genuinely needs more, validator caps catch it.
        """
        if not source_records:
            return []
        via = self.field(rel.via_field)
        if rel.cardinality in ("many_to_one", "one_to_one"):
            target_ids = [
                r[via.name]
                for r in source_records
                if isinstance(r, dict) and r.get(via.name)
            ]
            return self.store.get_many(
                rel.to_entity, list(dict.fromkeys(target_ids)),
            )
        # one_to_many or many_to_many: reverse lookup.
        from_pk = self.entity(rel.from_entity).primary_key
        source_ids = [
            r[from_pk]
            for r in source_records
            if isinstance(r, dict) and r.get(from_pk)
        ]
        if not source_ids:
            return []
        return self.store.find(
            rel.to_entity,
            [Filter(field=via.name, operator="in", value=list(source_ids))],
            limit=1000,
        )

    def aggregate(
        self,
        records: list[Record],
        op: AggOp,
        *,
        group_by_field: str | None = None,
        n: int | None = None,
        source_entity: str | None = None,
        cache: dict[tuple[str, str], Record | None] | None = None,
    ) -> dict[str, Any]:
        """Count / count_distinct / group_by_count / top_n.

        For group_by_count and top_n, raw keys are translated:
          * reference fields (e.g. assignment_group sys_ids) -> display
            names via display_name_of, so the response generator never
            sees `grp_cloud`.
          * value-mapped int codes (e.g. priority=1) -> labels ("Critical").
        The raw key is preserved as `key_raw` whenever a translation
        happened so audit / debug trails stay intact.
        """
        if op == "count":
            return {"count": len(records)}
        if op == "count_distinct":
            if not group_by_field:
                return {"count_distinct": 0}
            distinct = {
                r.get(group_by_field) for r in records if isinstance(r, dict)
            }
            distinct.discard(None)
            return {"count_distinct": len(distinct)}

        # group_by_count and top_n share the same grouping path.
        key = group_by_field or ""
        counts = Counter(
            r.get(key) for r in records if isinstance(r, dict)
        )

        # Look up the field metadata once so we know whether to resolve
        # sys_ids or translate value-map codes for display.
        field = None
        if key and source_entity:
            field_id = key if "." in key else f"{source_entity}.{key}"
            if self.has_field(field_id):
                field = self.field(field_id)

        groups: list[dict[str, Any]] = []
        for raw_key, count in counts.most_common():
            if raw_key is None:
                continue
            display_key: Any = raw_key
            translated = False
            if field is not None:
                if field.data_type == "reference" and field.references:
                    display_key = self.display_name_of(
                        field.references, str(raw_key), cache=cache,
                    )
                    translated = True
                elif (
                    field.value_map_id is not None
                    and isinstance(raw_key, int)
                ):
                    vm = self.value_map(field.value_map_id)
                    if raw_key in vm.forward:
                        display_key = vm.forward[raw_key]
                        translated = True
            entry: dict[str, Any] = {"key": display_key, "count": count}
            if translated:
                entry["key_raw"] = raw_key
            groups.append(entry)

        if op == "top_n" and n:
            groups = groups[:n]
        return {"groups": groups}

    def resolve(
        self,
        records: list[Record],
        source_entity: str,
        *,
        fields: list[str],
        include_relations: list[str] = (),  # type: ignore[assignment]
        apply_value_maps: bool = True,
        cache: dict[tuple[str, str], Record | None] | None = None,
    ) -> ResolveOutcome:
        """Project named fields out of each record, applying value maps
        and inlining related entities as display strings.

        include_relations may be either bare verb-ids ('reportedBy') —
        resolved against source_entity — or fully qualified
        ('incident.reportedBy'). Unknown relations are silently skipped
        (the validator runs first).

        Dangling references produce '[Unknown ...]' placeholders in the
        rendered records and a warning in the outcome.
        """
        relations_used: list[str] = []
        warnings: list[str] = []
        rendered: list[Record] = []

        for record in records:
            if not isinstance(record, dict):
                continue
            out: dict[str, Any] = {}
            for fname in fields:
                value = record.get(fname)
                if value is None:
                    out[fname] = None
                    continue
                field_id = f"{source_entity}.{fname}"
                if apply_value_maps and self.has_field(field_id):
                    fld = self.field(field_id)
                    if fld.value_map_id and isinstance(value, int):
                        vm = self.value_map(fld.value_map_id)
                        out[fname] = vm.forward.get(value, value)
                        continue
                out[fname] = value
            for rel_short in include_relations:
                full_rel_id = (
                    f"{source_entity}.{rel_short}"
                    if "." not in rel_short
                    else rel_short
                )
                if not self.has_relation(full_rel_id):
                    continue
                rel = self.relation(full_rel_id)
                relations_used.append(full_rel_id)
                via = self.field(rel.via_field)
                ref_id = record.get(via.name)
                if not ref_id:
                    continue
                display = self.display_name_of(
                    rel.to_entity, str(ref_id), cache=cache,
                )
                if display.startswith("[Unknown"):
                    warnings.append(
                        f"dangling reference: {via.name}={ref_id} -> {display}"
                    )
                out[rel_short] = display
            if "_score" in record:
                out["_score"] = record["_score"]
            rendered.append(out)

        return ResolveOutcome(
            records=rendered,
            relations_used=list(dict.fromkeys(relations_used)),
            warnings=warnings,
        )

    def kb_search(
        self,
        query: str,
        *,
        category_hint: str | None = None,
        top_k: int = 3,
    ) -> list[Record]:
        """Hybrid KB retrieval (FAISS + optional category narrowing).
        Returns [] when no KB retriever is bound — caller decides whether
        to treat that as a configuration error or as 'no KB results'."""
        if self._kb is None:
            return []
        return list(
            self._kb.search(query, category_hint=category_hint, top_k=top_k)
        )

    def propose_write(
        self,
        action: WriteAction,
        *,
        target_record: Record | None,
        fields: dict[str, Any],
        source_entity: str | None = None,
        nl_query: str = "",
        plan_id: str = "",
        ttl_seconds: int = 600,
    ) -> WriteProposal:
        """Construct a WriteProposal — does NOT mutate the data store. The
        confirm flow (write_path/executor.confirm) is the only path that
        actually writes, and it re-fetches the target to run the
        sys_updated_on optimistic-lock check before applying changes.
        """
        from src.write_path.proposal import new_proposal

        target_sys_id: str | None = None
        target_display: str
        current_values: dict[str, Any] | None = None
        diff: dict[str, dict[str, Any]] = {}

        if action == "update_incident":
            if not target_record:
                # The plan validator should catch this earlier; defensive.
                raise ValueError(
                    "update_incident requires a non-empty target_record",
                )
            target_sys_id = str(target_record.get("sys_id", ""))
            target_display = str(
                target_record.get("number")
                or target_record.get("name")
                or target_sys_id,
            )
            current_values = dict(target_record)
            entity = source_entity or "incident"
            for fname, new_val in fields.items():
                old_val = target_record.get(fname)
                old_display: Any = old_val
                fid = f"{entity}.{fname}"
                if self.has_field(fid):
                    fld = self.field(fid)
                    if fld.value_map_id and isinstance(old_val, int):
                        vm = self.value_map(fld.value_map_id)
                        old_display = vm.forward.get(old_val, old_val)
                diff[fname] = {"from": old_display, "to": new_val}
        else:
            # create_incident
            target_display = str(fields.get("short_description", "(new incident)"))
            diff = {k: {"from": None, "to": v} for k, v in fields.items()}

        return new_proposal(
            action=action,
            proposed_values=dict(fields),
            target_display=target_display,
            target_sys_id=target_sys_id,
            current_values=current_values,
            diff=diff,
            nl_query=nl_query,
            plan_id=plan_id,
            ttl_seconds=ttl_seconds,
        )

    # ----- plan driver ---------------------------------------------------

    async def execute_plan(
        self,
        plan: QueryPlan,
        query: str = "",
        request_id: str = "",
        *,
        scorer: RelationScorer | None = None,
    ) -> ExecutionResult:
        """Drive a validated QueryPlan through the graph's own methods,
        building an ExecutionTrace as we go.

        Plans with intent ``ambiguous`` or ``out_of_scope`` short-circuit
        — no execution attempted, empty trace returned. The API layer's
        route handler should not call execute_plan in those cases either;
        this is a defensive belt for direct callers (eval suite, MCP).

        Cross-call state is intentionally scoped to a local dict
        (``cache``) for sys_id-to-display lookups so the graph object
        stays free of per-request state.
        """
        import time

        from src.planner.plan_schema import (
            AggregateOp,
            FindOp,
            KBLookupOp,
            ResolveOp,
            TraverseOp,
            WriteProposalOp,
        )

        if plan.intent in ("ambiguous", "out_of_scope"):
            return ExecutionResult(
                output=None,
                trace=ExecutionTrace(
                    request_id=request_id, plan_id=request_id,
                ),
                warnings=[],
            )

        bindings: dict[str, Any] = {}
        var_entity: dict[str, str] = {}
        cache: dict[tuple[str, str], Record | None] = {}
        warnings: list[str] = []
        trace = ExecutionTrace(request_id=request_id, plan_id=request_id)
        t_total = time.perf_counter()

        for op in plan.operations:
            result: Any
            step: TraceStep
            try:
                if isinstance(op, FindOp):
                    result, step = self._exec_find(op)
                    var_entity[op.id] = op.entity
                elif isinstance(op, TraverseOp):
                    result, step = await self._exec_traverse(
                        op, bindings, var_entity, warnings,
                        scorer=scorer, user_query=query,
                    )
                elif isinstance(op, AggregateOp):
                    result, step = self._exec_aggregate(
                        op, bindings, var_entity, cache,
                    )
                elif isinstance(op, KBLookupOp):
                    result, step = self._exec_kb_lookup(op)
                    var_entity[op.id] = "kb_knowledge"
                elif isinstance(op, ResolveOp):
                    result, step = self._exec_resolve(
                        op, bindings, var_entity, warnings, cache,
                    )
                elif isinstance(op, WriteProposalOp):
                    result, step = self._exec_write_proposal(
                        op, bindings, var_entity, warnings, plan_id=request_id,
                        nl_query=query,
                    )
                else:
                    raise ExecutionError(f"unknown op type: {op.op!r}")
            except ExecutionError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ExecutionError(
                    f"op {op.id!r} ({op.op}) failed: {exc}",
                ) from exc
            bindings[op.id] = result
            trace.steps.append(step)

        trace.total_latency_ms = int((time.perf_counter() - t_total) * 1000)

        output = self._project_output(plan, bindings)
        return ExecutionResult(
            output=output, trace=trace, warnings=list(warnings),
        )

    # ----- per-op execution helpers --------------------------------------
    # These are private to execute_plan and exist to keep the dispatcher
    # readable. Each builds a TraceStep alongside the result.

    def _exec_find(
        self, op: FindOp,
    ) -> tuple[list[Record], TraceStep]:
        import time
        t0 = time.perf_counter()
        records = self.find(op.entity, list(op.filters), limit=op.limit)
        elapsed = int((time.perf_counter() - t0) * 1000)
        step = TraceStep(
            op_id=op.id,
            op_type="find",
            inputs={
                "entity": op.entity,
                "filters": [f.model_dump() for f in op.filters],
            },
            outputs_summary=f"{len(records)} {op.entity} record(s)",
            outputs_count=len(records),
            latency_ms=elapsed,
            target_entity=op.entity,
        )
        return records, step

    async def _exec_traverse(
        self,
        op: TraverseOp,
        bindings: dict[str, Any],
        var_entity: dict[str, str],
        warnings: list[str],
        *,
        scorer: RelationScorer | None,
        user_query: str,
    ) -> tuple[list[Record], TraceStep]:
        import time
        var_id = op.from_var.lstrip("$")
        source_records = bindings.get(var_id, [])
        if not isinstance(source_records, list):
            source_records = []

        source_entity = var_entity.get(var_id, "")
        if not source_entity:
            raise ExecutionError(
                f"op {op.id!r}: no source entity recorded for "
                f"{op.from_var!r}; upstream op did not bind one"
            )

        t0 = time.perf_counter()
        walk_result = await self.walk(
            source_records, source_entity, op.to_entity, list(op.path),
            filters_by_entity={
                k: list(v) for k, v in op.filters_by_entity.items()
            },
            scorer=scorer,
            user_query=user_query,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)

        # Track this op's binding entity for downstream resolve / aggregate.
        var_entity[op.id] = op.to_entity

        # Surface scorer info on the trace step so the inspector can show
        # which alternatives were considered and why a particular chain
        # was picked. None means no scoring happened (single shortest
        # path or planner-supplied path).
        scorer_choice = walk_result.scorer_choice
        scored_alternatives: list[dict[str, Any]] | None = None
        scoring_latency: int | None = None
        scoring_reasoning: str | None = None
        scoring_confidence: float | None = None
        if scorer_choice is not None:
            scored_alternatives = [
                {**c, "chosen": i == scorer_choice.chosen_index}
                for i, c in enumerate(scorer_choice.candidates)
            ]
            scoring_latency = scorer_choice.latency_ms
            scoring_reasoning = scorer_choice.reasoning
            scoring_confidence = scorer_choice.confidence
            if scorer_choice.confidence < 0.4:
                warnings.append(
                    f"op {op.id!r}: relation scorer picked path with "
                    f"low confidence ({scorer_choice.confidence:.2f}): "
                    f"{scorer_choice.reasoning}"
                )

        # Surface the engine's per-attempt log when fallback occurred
        # (top-ranked chain returned empty; engine tried the next). The
        # trace shows every chain attempted and which one yielded.
        attempted = (
            list(walk_result.attempted_paths)
            if walk_result.attempted_paths
            else None
        )

        step = TraceStep(
            op_id=op.id,
            op_type="traverse",
            inputs={
                "from": op.from_var,
                "to_entity": op.to_entity,
                "path": list(op.path),
                "filters_by_entity": {
                    k: [f.model_dump() for f in v]
                    for k, v in op.filters_by_entity.items()
                },
            },
            outputs_summary=(
                f"{len(walk_result.records)} {op.to_entity} via "
                f"{' -> '.join(walk_result.chain) or 'reflexive'}"
            ),
            outputs_count=len(walk_result.records),
            latency_ms=elapsed,
            graph_traversal=list(walk_result.chain),
            target_entity=op.to_entity,
            hops_filtered=list(walk_result.hops_filtered),
            scored_alternatives=scored_alternatives,
            scoring_latency_ms=scoring_latency,
            scoring_reasoning=scoring_reasoning,
            scoring_confidence=scoring_confidence,
            attempted_paths=attempted,
        )
        return walk_result.records, step

    def _exec_aggregate(
        self,
        op: AggregateOp,
        bindings: dict[str, Any],
        var_entity: dict[str, str],
        cache: dict[tuple[str, str], Record | None],
    ) -> tuple[dict[str, Any], TraceStep]:
        import time
        var_id = op.source.lstrip("$")
        source = bindings.get(var_id, [])
        if not isinstance(source, list):
            source = []
        src_entity = var_entity.get(var_id, "")
        t0 = time.perf_counter()
        result = self.aggregate(
            source, op.operation,
            group_by_field=op.group_by_field,
            n=op.n,
            source_entity=src_entity,
            cache=cache,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        # Aggregate results don't bind to an entity; downstream resolve
        # would not make sense, but we leave var_entity unset so resolve
        # validation catches misuse.
        if "count" in result:
            summary = f"count = {result['count']}"
        elif "count_distinct" in result:
            summary = f"count_distinct = {result['count_distinct']}"
        elif "groups" in result:
            summary = f"{len(result['groups'])} group(s)"
        else:
            summary = "aggregate"
        step = TraceStep(
            op_id=op.id,
            op_type="aggregate",
            inputs={"source": op.source, "operation": op.operation},
            outputs_summary=summary,
            outputs_count=len(source),
            latency_ms=elapsed,
        )
        return result, step

    def _exec_kb_lookup(
        self, op: KBLookupOp,
    ) -> tuple[list[Record], TraceStep]:
        import time
        t0 = time.perf_counter()
        records = self.kb_search(
            op.query, category_hint=op.category_hint, top_k=op.top_k,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        step = TraceStep(
            op_id=op.id,
            op_type="kb_lookup",
            inputs={
                "query": op.query,
                "category_hint": op.category_hint,
                "top_k": op.top_k,
            },
            outputs_summary=f"{len(records)} kb article(s)",
            outputs_count=len(records),
            latency_ms=elapsed,
            target_entity="kb_knowledge",
        )
        return records, step

    def _exec_resolve(
        self,
        op: ResolveOp,
        bindings: dict[str, Any],
        var_entity: dict[str, str],
        warnings: list[str],
        cache: dict[tuple[str, str], Record | None],
    ) -> tuple[list[Record], TraceStep]:
        import time
        src_var = op.source.lstrip("$")
        raw = bindings.get(src_var, [])
        src_entity = var_entity.get(src_var, "")
        if not isinstance(raw, list):
            raw = []
        t0 = time.perf_counter()
        outcome = self.resolve(
            raw, src_entity,
            fields=list(op.fields),
            include_relations=list(op.include_relations),
            apply_value_maps=op.apply_value_maps,
            cache=cache,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        if outcome.warnings:
            warnings.extend(outcome.warnings)
        # Resolve passes the source entity through to its variable so a
        # subsequent op can keep traversing.
        if src_entity:
            var_entity[op.id] = src_entity
        step = TraceStep(
            op_id=op.id,
            op_type="resolve",
            inputs={
                "source": op.source,
                "fields": list(op.fields),
                "include_relations": list(op.include_relations),
            },
            outputs_summary=f"{len(outcome.records)} rendered record(s)",
            outputs_count=len(outcome.records),
            latency_ms=elapsed,
            warnings=list(outcome.warnings),
            graph_traversal=list(outcome.relations_used),
            target_entity=src_entity,
        )
        return outcome.records, step

    def _exec_write_proposal(
        self,
        op: WriteProposalOp,
        bindings: dict[str, Any],
        var_entity: dict[str, str],
        warnings: list[str],
        *,
        plan_id: str = "",
        nl_query: str = "",
    ) -> tuple[WriteProposal, TraceStep]:
        import time
        target_record: Record | None = None
        source_entity = None
        if op.target_var:
            var_id = op.target_var.lstrip("$")
            target_list = bindings.get(var_id, [])
            if isinstance(target_list, list) and target_list:
                target_record = target_list[0]
                source_entity = var_entity.get(var_id)
            else:
                warnings.append(
                    f"write_proposal {op.id!r}: target_var {op.target_var} "
                    f"resolved to no record",
                )
        t0 = time.perf_counter()
        proposal = self.propose_write(
            op.action,
            target_record=target_record,
            fields=dict(op.fields),
            source_entity=source_entity or "incident",
            nl_query=nl_query,
            plan_id=plan_id,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        step = TraceStep(
            op_id=op.id,
            op_type="write_proposal",
            inputs={
                "action": op.action,
                "target_var": op.target_var,
                "fields": dict(op.fields),
            },
            outputs_summary=f"proposal token={proposal.token}",
            outputs_count=1,
            latency_ms=elapsed,
        )
        return proposal, step

    def _project_output(
        self, plan: QueryPlan, bindings: dict[str, Any],
    ) -> Any:
        spec = plan.output_spec
        if spec is None:
            return None
        if spec.final_var not in bindings:
            raise ExecutionError(
                f"output_spec.final_var {spec.final_var!r} was not "
                f"produced by the plan",
            )
        raw = bindings[spec.final_var]
        if spec.format == "single_item":
            if isinstance(raw, list):
                return raw[0] if raw else None
            return raw
        if spec.format == "list":
            if isinstance(raw, list):
                return raw[: spec.max_items_shown]
            return raw
        if spec.format == "scalar":
            if isinstance(raw, dict):
                for v in raw.values():
                    if not isinstance(v, list):
                        return v
            return raw
        return raw  # kb_answer, write_confirmation pass through

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
