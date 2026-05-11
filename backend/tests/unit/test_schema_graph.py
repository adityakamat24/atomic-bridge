from __future__ import annotations

from src.core.schema_graph import (
    EDGE_FROM,
    EDGE_HAS_FIELD,
    EDGE_INVERSE_OF,
    EDGE_MAPS_THROUGH,
    EDGE_TO,
    EDGE_VIA,
    Entity,
    FieldNode,
    Relation,
    SchemaGraph,
    ValueMap,
)


def _build_minimal() -> SchemaGraph:
    """A handcrafted 2-entity, 1-relation, 1-value-map graph used for unit tests
    that should NOT depend on the real schema.yaml content.
    """
    g = SchemaGraph()
    g.add_value_map(ValueMap(id="state_map", description="", forward={1: "Open", 2: "Closed"}))
    g.add_entity(
        Entity(
            id="ticket",
            display_name="Ticket",
            description="A test ticket",
            primary_key="sys_id",
            table_name="ticket",
            data_path="tickets.json",
        )
    )
    g.add_entity(
        Entity(
            id="agent",
            display_name="Agent",
            description="Support agent",
            primary_key="sys_id",
            table_name="agent",
            data_path="agents.json",
        )
    )
    g.add_field(
        FieldNode(
            id="ticket.state",
            entity_id="ticket",
            name="state",
            display_name="Status",
            data_type="integer",
            value_map_id="state_map",
        )
    )
    g.add_field(
        FieldNode(
            id="ticket.assigned_to",
            entity_id="ticket",
            name="assigned_to",
            display_name="Assignee",
            data_type="reference",
            references="agent",
        )
    )
    g.add_field(
        FieldNode(
            id="agent.name",
            entity_id="agent",
            name="name",
            display_name="Name",
            data_type="string",
        )
    )
    g.add_relation(
        Relation(
            id="ticket.assignedTo",
            verb_phrase="assigned to",
            from_entity="ticket",
            to_entity="agent",
            via_field="ticket.assigned_to",
            cardinality="many_to_one",
            inverse_relation_id="agent.handles",
        )
    )
    g.add_relation(
        Relation(
            id="agent.handles",
            verb_phrase="handles",
            from_entity="agent",
            to_entity="ticket",
            via_field="ticket.assigned_to",
            cardinality="one_to_many",
            inverse_relation_id="ticket.assignedTo",
        )
    )
    g.link_inverses()
    return g


# ---------- ValueMap ----------------------------------------------------------


def test_value_map_to_display() -> None:
    vm = ValueMap(id="m", description="", forward={1: "New", 2: "In Progress"})
    assert vm.to_display(1) == "New"
    assert vm.to_display(2) == "In Progress"


def test_value_map_from_display() -> None:
    vm = ValueMap(id="m", description="", forward={1: "New", 2: "In Progress"})
    assert vm.from_display("New") == 1
    assert vm.from_display("In Progress") == 2


def test_value_map_fuzzy_normalises_separators_and_case() -> None:
    vm = ValueMap(id="m", description="", forward={1: "In Progress", 2: "On Hold"})
    assert vm.fuzzy_from_display("in progress") == 1
    assert vm.fuzzy_from_display("In-Progress") == 1
    assert vm.fuzzy_from_display("in_progress") == 1
    assert vm.fuzzy_from_display("InProgress") == 1
    assert vm.fuzzy_from_display("on hold") == 2
    assert vm.fuzzy_from_display("paused") is None


# ---------- Edges -------------------------------------------------------------


def test_add_field_creates_has_field_and_maps_through_edges() -> None:
    g = _build_minimal()
    edges = [d["type"] for _, _, d in g._g.edges("ticket", data=True)]  # noqa: SLF001
    assert EDGE_HAS_FIELD in edges
    edges = [d["type"] for _, _, d in g._g.edges("ticket.state", data=True)]  # noqa: SLF001
    assert EDGE_MAPS_THROUGH in edges


def test_add_relation_creates_from_to_via_edges() -> None:
    g = _build_minimal()
    types = {d["type"] for _, _, d in g._g.edges("ticket.assignedTo", data=True)}  # noqa: SLF001
    assert {EDGE_FROM, EDGE_TO, EDGE_VIA} <= types


def test_link_inverses_creates_inverse_of_edges() -> None:
    g = _build_minimal()
    types = {d["type"] for _, _, d in g._g.edges("ticket.assignedTo", data=True)}  # noqa: SLF001
    assert EDGE_INVERSE_OF in types


# ---------- Lookups ----------------------------------------------------------


def test_entity_field_relation_value_map_lookups() -> None:
    g = _build_minimal()
    assert g.entity("ticket").display_name == "Ticket"
    assert g.field("ticket.state").value_map_id == "state_map"
    assert g.relation("ticket.assignedTo").to_entity == "agent"
    assert g.value_map("state_map").to_display(1) == "Open"


def test_relations_from_and_to() -> None:
    g = _build_minimal()
    from_ticket = [r.id for r in g.relations_from("ticket")]
    assert from_ticket == ["ticket.assignedTo"]
    to_agent = [r.id for r in g.relations_to("agent")]
    assert to_agent == ["ticket.assignedTo"]


def test_fields_of_returns_in_insertion_order() -> None:
    g = _build_minimal()
    field_names = [f.name for f in g.fields_of("ticket")]
    assert field_names == ["state", "assigned_to"]


# ---------- shortest_relation_path -------------------------------------------


def test_shortest_relation_path_same_entity_returns_empty() -> None:
    g = _build_minimal()
    assert g.shortest_relation_path("ticket", "ticket") == []


def test_shortest_relation_path_one_hop() -> None:
    g = _build_minimal()
    path = g.shortest_relation_path("ticket", "agent")
    assert path is not None
    assert [r.id for r in path] == ["ticket.assignedTo"]


def test_shortest_relation_path_unreachable_returns_none() -> None:
    g = SchemaGraph()
    g.add_entity(Entity(id="a", display_name="A", table_name="a", data_path="a.json"))
    g.add_entity(Entity(id="b", display_name="B", table_name="b", data_path="b.json"))
    assert g.shortest_relation_path("a", "b") is None


def test_shortest_relation_path_unknown_entity_returns_none() -> None:
    g = _build_minimal()
    assert g.shortest_relation_path("ghost", "agent") is None
    assert g.shortest_relation_path("ticket", "ghost") is None


# ---------- expand_subgraph -------------------------------------------------


def test_expand_subgraph_zero_hops_returns_only_seed() -> None:
    g = _build_minimal()
    assert g.expand_subgraph(["ticket"], max_hops=0) == ["ticket"]


def test_expand_subgraph_follows_outbound_relations() -> None:
    g = _build_minimal()
    out = g.expand_subgraph(["ticket"], max_hops=1)
    assert set(out) == {"ticket", "agent"}


def test_expand_subgraph_follows_inbound_relations() -> None:
    """Seed `agent`, expansion must reach `ticket` through the inbound
    `ticket.assignedTo` relation even when no outbound relation is defined.
    Production schemas frequently omit one side of an inverse pair, so
    inbound-following is what makes the filter safe at scale."""
    g = SchemaGraph()
    g.add_entity(
        Entity(id="ticket", display_name="T", table_name="ticket", data_path="t.json")
    )
    g.add_entity(
        Entity(id="agent", display_name="A", table_name="agent", data_path="a.json")
    )
    g.add_field(
        FieldNode(
            id="ticket.assigned_to",
            entity_id="ticket",
            name="assigned_to",
            display_name="Assignee",
            data_type="reference",
            references="agent",
        )
    )
    g.add_relation(
        Relation(
            id="ticket.assignedTo",
            verb_phrase="assigned to",
            from_entity="ticket",
            to_entity="agent",
            via_field="ticket.assigned_to",
            cardinality="many_to_one",
        )
    )
    out = g.expand_subgraph(["agent"], max_hops=1)
    assert set(out) == {"agent", "ticket"}


def test_expand_subgraph_ignores_unknown_seeds() -> None:
    g = _build_minimal()
    assert g.expand_subgraph(["ghost"], max_hops=2) == []


def test_expand_subgraph_preserves_seed_order() -> None:
    g = _build_minimal()
    out = g.expand_subgraph(["agent", "ticket"], max_hops=0)
    assert out == ["agent", "ticket"]


# ---------- counts and integrity ---------------------------------------------


def test_counts_match_inserted_nodes() -> None:
    g = _build_minimal()
    counts = g.counts()
    assert counts == {"entities": 2, "fields": 3, "value_maps": 1, "relations": 2}


def test_integrity_issues_clean_on_minimal_graph() -> None:
    g = _build_minimal()
    assert g.integrity_issues() == []


def test_integrity_flags_dangling_reference() -> None:
    g = SchemaGraph()
    g.add_entity(Entity(id="a", display_name="A", table_name="a", data_path="a.json"))
    g.add_field(
        FieldNode(
            id="a.f",
            entity_id="a",
            name="f",
            display_name="F",
            data_type="reference",
            references="missing_entity",
        )
    )
    issues = g.integrity_issues()
    assert any("missing_entity" in i for i in issues)


# ---------- to_visjs ---------------------------------------------------------


def test_to_visjs_includes_entities_value_maps_and_relations() -> None:
    g = _build_minimal()
    out = g.to_visjs()
    node_ids = {n["id"] for n in out["nodes"]}
    assert {"ticket", "agent", "state_map"} <= node_ids
    edge_ids = {e["id"] for e in out["edges"]}
    assert {"ticket.assignedTo", "agent.handles"} <= edge_ids


def test_to_visjs_highlight_marks_path() -> None:
    g = _build_minimal()
    out = g.to_visjs(highlight_path=["ticket.assignedTo"])
    highlighted = [e["id"] for e in out["edges"] if e["highlighted"]]
    assert highlighted == ["ticket.assignedTo"]


def test_to_visjs_connects_value_maps_to_their_owning_entity() -> None:
    """Value-map nodes must not float disconnected in the visualization.
    Project Field→MAPS_THROUGH→ValueMap as Entity→ValueMap so the user can see
    that `incident_state` belongs to `incident.state`, etc."""
    g = _build_minimal()
    out = g.to_visjs()
    vm_edges = [e for e in out["edges"] if e["cardinality"] == "value_map"]
    # ticket.state has value_map=state_map in the minimal graph
    assert any(
        e["from"] == "ticket" and e["to"] == "state_map" for e in vm_edges
    ), f"missing ticket→state_map edge, got {vm_edges}"
    # Edge labels carry the field name so a reader knows WHICH field uses the map
    assert any("state" in e["label"] for e in vm_edges)
