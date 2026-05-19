"""Tests for SchemaGraph.shortest_relation_paths and SchemaGraph.validate_path.

These are the foundational graph-algorithm methods the planner-emitted
declarative `traverse to_entity=X path=[...]` op depends on. The plan
validator calls validate_path to reject any chain that isn't shortest /
doesn't connect / mentions an invented relation. shortest_relation_paths
is used by the schema debugger and is the source of truth for what the
validator considers "shortest".
"""
from __future__ import annotations

from pathlib import Path

from src.core.schema_graph import Relation
from src.core.schema_loader import load, loads

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"


# ---------------------------------------------------------------------------
# shortest_relation_paths — happy paths
# ---------------------------------------------------------------------------


def test_shortest_paths_reflexive_returns_single_empty_chain() -> None:
    graph = load(REAL_SCHEMA)
    paths = graph.shortest_relation_paths("sys_user", "sys_user")
    assert paths == [[]], "from == to should yield exactly one empty chain"


def test_shortest_paths_single_hop_caller_or_assignee() -> None:
    """sys_user -> incident has TWO relations at length 1: incidentsReported
    and incidentsAssigned. Both should appear as tied-shortest chains."""
    graph = load(REAL_SCHEMA)
    paths = graph.shortest_relation_paths("sys_user", "incident")
    assert len(paths) == 2
    rel_ids = {chain[0].id for chain in paths}
    assert rel_ids == {"sys_user.incidentsReported", "sys_user.incidentsAssigned"}
    # All chains are length 1.
    assert {len(chain) for chain in paths} == {1}


def test_shortest_paths_incident_to_user_two_relations() -> None:
    """Inverse direction: incident -> sys_user has reportedBy + assignedTo."""
    graph = load(REAL_SCHEMA)
    paths = graph.shortest_relation_paths("incident", "sys_user")
    rel_ids = {chain[0].id for chain in paths}
    assert rel_ids == {"incident.reportedBy", "incident.assignedTo"}


def test_shortest_paths_kb_via_category_bridge() -> None:
    """sys_user -> kb_knowledge crosses the Category bridge in 3 hops.
    There should be 2 chains (caller-side and assignee-side parallel
    relations on the first hop)."""
    graph = load(REAL_SCHEMA)
    paths = graph.shortest_relation_paths("sys_user", "kb_knowledge")
    assert all(len(chain) == 3 for chain in paths), (
        "the shortest sys_user -> kb_knowledge chain is 3 hops"
    )
    # Both chains end at category.kbArticlesInCategory and go through
    # incident.inCategory.
    for chain in paths:
        assert chain[1].id == "incident.inCategory"
        assert chain[2].id == "category.kbArticlesInCategory"
    first_hops = {chain[0].id for chain in paths}
    assert first_hops == {
        "sys_user.incidentsReported",
        "sys_user.incidentsAssigned",
    }


def test_shortest_paths_real_schema_is_strongly_connected() -> None:
    """The production schema is strongly connected because every relation
    has a first-class inverse, so reachability holds in both directions.
    This is asserted explicitly to document the assumption that the
    shortest-path algorithm relies on. The unreachable case is exercised
    on a synthetic schema below."""
    graph = load(REAL_SCHEMA)
    for src in ("sys_user", "incident", "sys_user_group", "kb_knowledge", "category"):
        for dst in ("sys_user", "incident", "sys_user_group", "kb_knowledge", "category"):
            paths = graph.shortest_relation_paths(src, dst)
            assert paths, f"expected at least one path {src!r} -> {dst!r}"


def test_shortest_paths_unknown_entity_returns_empty() -> None:
    graph = load(REAL_SCHEMA)
    assert graph.shortest_relation_paths("does_not_exist", "incident") == []
    assert graph.shortest_relation_paths("sys_user", "does_not_exist") == []


def test_shortest_paths_respects_max_hops() -> None:
    """sys_user -> kb_knowledge is 3 hops; max_hops=2 should reject it."""
    graph = load(REAL_SCHEMA)
    assert graph.shortest_relation_paths(
        "sys_user", "kb_knowledge", max_hops=2,
    ) == []


def test_shortest_paths_returns_deterministic_order() -> None:
    """Insertion order should be respected; two calls return the same shape."""
    graph = load(REAL_SCHEMA)
    a = graph.shortest_relation_paths("sys_user", "incident")
    b = graph.shortest_relation_paths("sys_user", "incident")
    assert [c[0].id for c in a] == [c[0].id for c in b]


# ---------------------------------------------------------------------------
# shortest_relation_paths — synthetic graph variations
# ---------------------------------------------------------------------------


_DIAMOND_YAML = """
value_maps: []
entities:
  - id: a
    table_name: a
    data_path: a.json
    fields:
      - {name: sys_id, data_type: string}
      - {name: to_b, data_type: reference, references: b}
      - {name: to_c, data_type: reference, references: c}
  - id: b
    table_name: b
    data_path: b.json
    fields:
      - {name: sys_id, data_type: string}
      - {name: to_d, data_type: reference, references: d}
  - id: c
    table_name: c
    data_path: c.json
    fields:
      - {name: sys_id, data_type: string}
      - {name: to_d, data_type: reference, references: d}
  - id: d
    table_name: d
    data_path: d.json
    fields:
      - {name: sys_id, data_type: string}
relations:
  - id: a.toB
    verb_phrase: "to b"
    from_entity: a
    to_entity: b
    via_field: a.to_b
    cardinality: many_to_one
  - id: a.toC
    verb_phrase: "to c"
    from_entity: a
    to_entity: c
    via_field: a.to_c
    cardinality: many_to_one
  - id: b.toD
    verb_phrase: "b to d"
    from_entity: b
    to_entity: d
    via_field: b.to_d
    cardinality: many_to_one
  - id: c.toD
    verb_phrase: "c to d"
    from_entity: c
    to_entity: d
    via_field: c.to_d
    cardinality: many_to_one
"""


def test_diamond_two_distinct_two_hop_paths() -> None:
    """Diamond graph: a -> b -> d AND a -> c -> d. Two distinct 2-hop chains."""
    graph = loads(_DIAMOND_YAML)
    paths = graph.shortest_relation_paths("a", "d")
    assert len(paths) == 2
    chains_ids = sorted([rel.id for rel in chain] for chain in paths)
    assert chains_ids == sorted([
        ["a.toB", "b.toD"],
        ["a.toC", "c.toD"],
    ])


_SELF_LOOP_YAML = """
value_maps: []
entities:
  - id: u
    table_name: u
    data_path: u.json
    fields:
      - {name: sys_id, data_type: string}
      - {name: manager, data_type: reference, references: u}
  - id: t
    table_name: t
    data_path: t.json
    fields:
      - {name: sys_id, data_type: string}
      - {name: owner, data_type: reference, references: u}
relations:
  - id: u.manages
    verb_phrase: "manages"
    from_entity: u
    to_entity: u
    via_field: u.manager
    cardinality: one_to_many
  - id: u.managedBy
    verb_phrase: "managed by"
    from_entity: u
    to_entity: u
    via_field: u.manager
    cardinality: many_to_one
    inverse_relation_id: u.manages
  - id: u.owns
    verb_phrase: "owns"
    from_entity: u
    to_entity: t
    via_field: t.owner
    cardinality: one_to_many
"""


def test_self_loops_do_not_make_target_unreachable() -> None:
    """Self-relations like manages/managedBy should not interfere with
    a different target (the shortest path from u -> t is still 1 hop)."""
    graph = loads(_SELF_LOOP_YAML)
    paths = graph.shortest_relation_paths("u", "t")
    assert len(paths) == 1
    assert paths[0][0].id == "u.owns"


def test_self_loop_reflexive_is_empty_chain() -> None:
    graph = loads(_SELF_LOOP_YAML)
    assert graph.shortest_relation_paths("u", "u") == [[]]


_ORPHAN_YAML = """
value_maps: []
entities:
  - id: a
    table_name: a
    data_path: a.json
    fields:
      - {name: sys_id, data_type: string}
      - {name: to_b, data_type: reference, references: b}
  - id: b
    table_name: b
    data_path: b.json
    fields:
      - {name: sys_id, data_type: string}
  - id: orphan
    table_name: orphan
    data_path: orphan.json
    fields:
      - {name: sys_id, data_type: string}
relations:
  - id: a.toB
    verb_phrase: "to b"
    from_entity: a
    to_entity: b
    via_field: a.to_b
    cardinality: many_to_one
"""


def test_unreachable_pair_returns_empty() -> None:
    """orphan has no inbound or outbound relations; nothing reaches it."""
    graph = loads(_ORPHAN_YAML)
    assert graph.shortest_relation_paths("a", "orphan") == []
    assert graph.shortest_relation_paths("orphan", "b") == []


# ---------------------------------------------------------------------------
# validate_path — happy paths
# ---------------------------------------------------------------------------


def test_validate_path_accepts_valid_single_hop() -> None:
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "sys_user", "incident", ["sys_user.incidentsAssigned"],
    )
    assert errors == []


def test_validate_path_accepts_valid_three_hop_chain() -> None:
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "sys_user", "kb_knowledge",
        [
            "sys_user.incidentsAssigned",
            "incident.inCategory",
            "category.kbArticlesInCategory",
        ],
    )
    assert errors == []


def test_validate_path_reflexive_empty_path_ok() -> None:
    graph = load(REAL_SCHEMA)
    assert graph.validate_path("sys_user", "sys_user", []) == []


# ---------------------------------------------------------------------------
# validate_path — error cases
# ---------------------------------------------------------------------------


def test_validate_path_rejects_unknown_from_entity() -> None:
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path("does_not_exist", "incident", ["x"])
    assert any("from_entity" in e for e in errors)


def test_validate_path_rejects_unknown_to_entity() -> None:
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path("sys_user", "does_not_exist", ["x"])
    assert any("to_entity" in e for e in errors)


def test_validate_path_rejects_invented_relation_id() -> None:
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "sys_user", "incident", ["sys_user.notARealRelation"],
    )
    assert any("not in schema" in e for e in errors)


def test_validate_path_rejects_chain_starting_at_wrong_entity() -> None:
    """Declared from_entity=sys_user, but path[0]=incident.reportedBy
    starts at incident."""
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "sys_user", "incident", ["incident.reportedBy"],
    )
    assert any("path[0]" in e and "starts at" in e for e in errors)


def test_validate_path_rejects_chain_ending_at_wrong_target() -> None:
    """Chain ends at sys_user but caller declares to_entity=sys_user_group."""
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "sys_user", "sys_user_group", ["sys_user.manages"],
    )
    assert any("path[-1]" in e and "ends at" in e for e in errors)


def test_validate_path_rejects_broken_chain() -> None:
    """Two hops that don't connect: incidentsReported ends at incident, but
    sys_user.manages starts at sys_user, not incident."""
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "sys_user", "sys_user_group",
        ["sys_user.incidentsReported", "sys_user.manages"],
    )
    assert any("chain break" in e for e in errors)


def test_validate_path_rejects_empty_path_for_non_reflexive() -> None:
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path("sys_user", "incident", [])
    assert any("empty path" in e for e in errors)


def test_validate_path_accepts_self_relation_walk() -> None:
    """When from == to, a non-empty path is valid as long as the chain
    is a real self-loop walk (same entity type, different records). The
    canonical example: `sys_user.managedBy` walks from a user to their
    manager — both are sys_user records, but the chain is meaningful.
    The old "reflexive must be empty" gate was a bug that blocked the
    PDF's "who is X's manager?" queries entirely."""
    graph = load(REAL_SCHEMA)
    # 1-hop self-relation
    errors = graph.validate_path(
        "sys_user", "sys_user", ["sys_user.managedBy"],
    )
    assert errors == [], (
        f"self-relation walks must be accepted; got {errors}"
    )
    # The inverse direction (manages -> directs) too
    errors = graph.validate_path(
        "sys_user", "sys_user", ["sys_user.manages"],
    )
    assert errors == []


def test_validate_path_rejects_chain_ending_at_wrong_entity_for_reflexive() -> None:
    """A non-empty chain that doesn't actually end at the declared
    to_entity is still rejected, even when from == to."""
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "sys_user", "sys_user", ["sys_user.incidentsReported"],
    )
    # The chain ends at incident, not sys_user → mismatch error.
    assert any("ends at" in e for e in errors)


def test_validate_path_accepts_non_shortest_chain_within_max_hops() -> None:
    """The validator accepts any simple chain within max_hops so the
    relation scorer can rank by user phrasing. "Ravi's team" needs a
    2-hop chain to sys_user_group alongside the 1-hop `managesGroups`.
    Disconnected / mismatched / invented chains and chains beyond
    max_hops are still rejected."""
    graph = load(REAL_SCHEMA)
    # 3-hop chain from sys_user to incident going through sys_user_group.
    # The shortest sys_user -> incident is 1 hop; this 3-hop chain is a
    # valid simple chain and must be accepted.
    longer = [
        "sys_user.incidentsAssigned",
        "incident.handledBy",
        "sys_user_group.incidentsHandled",
    ]
    errors = graph.validate_path("sys_user", "incident", longer)
    assert errors == [], (
        f"expected non-shortest chain to be accepted; got errors: {errors}"
    )


def test_validate_path_rejects_chain_beyond_max_hops() -> None:
    """A chain longer than max_hops is rejected (resource bound, not
    a shortest-path constraint)."""
    graph = load(REAL_SCHEMA)
    too_long = [
        "sys_user.incidentsReported",
        "incident.reportedBy",
        "sys_user.incidentsAssigned",
        "incident.assignedTo",
        "sys_user.incidentsReported",
    ]
    errors = graph.validate_path(
        "sys_user", "incident", too_long, max_hops=4,
    )
    assert any("max_hops" in e for e in errors)


def test_validate_path_rejects_unreachable_pair() -> None:
    """kb_knowledge -> sys_user has no outbound path."""
    graph = load(REAL_SCHEMA)
    errors = graph.validate_path(
        "kb_knowledge", "sys_user", ["kb_knowledge.inCategory"],
    )
    # The chain doesn't reach sys_user, and there's no path either way.
    assert errors


def test_validate_path_returns_typed_list_of_relations() -> None:
    """shortest_relation_paths returns Relation objects, not just IDs."""
    graph = load(REAL_SCHEMA)
    paths = graph.shortest_relation_paths("sys_user", "incident")
    for chain in paths:
        for rel in chain:
            assert isinstance(rel, Relation)
            assert rel.id
            assert rel.from_entity
            assert rel.to_entity
            assert rel.verb_phrase
