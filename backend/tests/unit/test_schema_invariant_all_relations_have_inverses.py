"""Schema invariant: every Relation in data/schema.yaml must declare a
valid inverse_relation_id whose target is itself a declared Relation
pointing in the opposite direction.

WHY THIS INVARIANT EXISTS
=========================

The graph executes natural-language plans by walking declarative
`traverse to_entity=X path=[rel1, rel2, ...]` operations. SchemaGraph
computes the SHORTEST relation chain between two entities using
``nx.all_shortest_paths`` on an entity-only projection — see
``shortest_relation_paths`` and ``validate_path`` in
``backend/src/core/schema_graph.py``.

The projection follows only OUTBOUND relations from each entity (a
relation's ``from_entity`` -> ``to_entity``). For this to find every
semantically-meaningful chain, every relation must have a first-class
inverse declared in the opposite direction. If a relation existed only
in one direction (e.g., ``incident.reportedBy`` without
``sys_user.incidentsReported``), the planner would not be able to walk
``sys_user -> incident`` even though the underlying field
``incident.caller_id`` carries that exact information.

This test fails LOUDLY if anyone removes or forgets the inverse on
future relations. The audit trail that produced this test is documented
in ``tasks/survey-gaps.md`` (the old schema had exactly this bug — the
sys_user_group.managedBy <-> sys_user.managesGroups pair was missing
the user-side relation).
"""
from __future__ import annotations

from pathlib import Path

from src.core.schema_loader import load

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"


def test_every_relation_has_an_inverse_relation_id() -> None:
    graph = load(REAL_SCHEMA)
    relations = graph.all_relations()
    assert relations, "expected the real schema to declare relations"
    for rel in relations:
        assert rel.inverse_relation_id is not None, (
            f"relation {rel.id!r} has no inverse_relation_id declared. "
            f"Every relation must have a first-class inverse so the graph's "
            f"shortest-path algorithm (which only follows outbound edges) "
            f"can find chains regardless of which side the user names."
        )


def test_every_inverse_actually_exists() -> None:
    graph = load(REAL_SCHEMA)
    for rel in graph.all_relations():
        assert rel.inverse_relation_id is not None
        assert graph.has_relation(rel.inverse_relation_id), (
            f"relation {rel.id!r} declares inverse_relation_id "
            f"{rel.inverse_relation_id!r}, but that relation is not "
            f"defined in the schema. Define both directions or remove "
            f"the inverse_relation_id reference."
        )


def test_inverses_actually_point_the_other_way() -> None:
    """For relation A -> B, its inverse must be a relation B -> A whose
    own inverse_relation_id points back to the original. This catches
    the case where someone declares an 'inverse' that goes in the wrong
    direction or that itself has the wrong back-reference."""
    graph = load(REAL_SCHEMA)
    for rel in graph.all_relations():
        assert rel.inverse_relation_id is not None
        inverse = graph.relation(rel.inverse_relation_id)
        assert inverse.from_entity == rel.to_entity, (
            f"relation {rel.id!r} (from={rel.from_entity!r}, "
            f"to={rel.to_entity!r}) has inverse {inverse.id!r} which "
            f"starts at {inverse.from_entity!r}, expected {rel.to_entity!r}"
        )
        assert inverse.to_entity == rel.from_entity, (
            f"relation {rel.id!r} (from={rel.from_entity!r}, "
            f"to={rel.to_entity!r}) has inverse {inverse.id!r} which "
            f"ends at {inverse.to_entity!r}, expected {rel.from_entity!r}"
        )
        assert inverse.inverse_relation_id == rel.id, (
            f"asymmetric inverse: {rel.id!r} -> {inverse.id!r}, but "
            f"{inverse.id!r}'s inverse_relation_id is "
            f"{inverse.inverse_relation_id!r}, not {rel.id!r}"
        )


def test_inverses_share_the_same_via_field() -> None:
    """A relation and its inverse describe the SAME underlying field in
    the data, just from opposite ends. If they had different via_fields,
    the executor's reverse-traversal logic would walk against different
    references and the inverse would not actually be the inverse."""
    graph = load(REAL_SCHEMA)
    for rel in graph.all_relations():
        assert rel.inverse_relation_id is not None
        inverse = graph.relation(rel.inverse_relation_id)
        assert inverse.via_field == rel.via_field, (
            f"relation {rel.id!r} uses via_field {rel.via_field!r} but "
            f"its declared inverse {inverse.id!r} uses "
            f"{inverse.via_field!r}. Both directions must reference the "
            f"same physical field."
        )


def test_cardinality_pairs_are_consistent() -> None:
    """A many_to_one relation's inverse must be one_to_many; a
    one_to_one's inverse is one_to_one; a many_to_many's inverse is
    many_to_many. Mismatch indicates a misspecified schema."""
    graph = load(REAL_SCHEMA)
    valid_pairs = {
        ("many_to_one", "one_to_many"),
        ("one_to_many", "many_to_one"),
        ("one_to_one", "one_to_one"),
        ("many_to_many", "many_to_many"),
    }
    for rel in graph.all_relations():
        assert rel.inverse_relation_id is not None
        inverse = graph.relation(rel.inverse_relation_id)
        pair = (rel.cardinality, inverse.cardinality)
        assert pair in valid_pairs, (
            f"relation {rel.id!r} cardinality is {rel.cardinality!r} "
            f"but inverse {inverse.id!r} cardinality is "
            f"{inverse.cardinality!r}. Valid pairs are: "
            f"many_to_one<->one_to_many, one_to_one<->one_to_one, "
            f"many_to_many<->many_to_many."
        )
