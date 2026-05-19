"""Tests for the execution methods that live directly on SchemaGraph.

The graph IS the executor in the redesigned architecture — find, walk
(tested separately in step 5), aggregate, resolve, kb_search, and
propose_write are graph methods that internally call into the bound
DataStore / KBRetriever. Tests here exercise each method in isolation
against the real schema and a real in-memory store; step 6 covers them
end-to-end via execute_plan.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from src.core.data_store import Filter
from src.core.in_memory_store import InMemoryStore
from src.core.schema_graph import (
    AggOp,
    ResolveOutcome,
    SchemaGraph,
    WriteAction,
)
from src.core.schema_loader import load
from src.knowledge.embeddings import EmbeddingClient
from src.knowledge.indexer import KBIndexer
from src.knowledge.retriever import KBRetriever

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _KeywordEmbedder:
    """Deterministic 8-dim presence-of-keyword embedder used by KB tests
    so we don't need to load sentence-transformers in this suite."""

    dimension = 8
    _vocab = ["vpn", "outlook", "share", "badge", "laptop", "kb", "category", "test"]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, t in enumerate(texts):
            tl = t.lower()
            for j, kw in enumerate(self._vocab):
                if kw in tl:
                    out[i, j] = 1.0
            # Normalise so inner-product behaves like cosine sim.
            n = np.linalg.norm(out[i])
            if n > 0:
                out[i] /= n
        return out


@pytest.fixture
def graph() -> SchemaGraph:
    g = load(REAL_SCHEMA)
    store = InMemoryStore(g, DATA_DIR)
    emb: EmbeddingClient = _KeywordEmbedder()  # type: ignore[assignment]
    kb = KBRetriever(store, KBIndexer(emb))
    g.bind_runtime(store=store, kb=kb)
    return g


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def test_find_returns_records_for_known_entity(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [])
    assert len(incidents) >= 5
    for r in incidents:
        assert "sys_id" in r and "number" in r


def test_find_applies_filters(graph: SchemaGraph) -> None:
    """`state` is value-mapped; the store accepts display strings."""
    open_incidents = graph.find(
        "incident",
        [Filter(field="state", operator="in",
                value=["New", "In Progress", "On Hold"])],
    )
    states = {r["state"] for r in open_incidents}
    assert states.issubset({1, 2, 3})


def test_find_unknown_entity_returns_empty(graph: SchemaGraph) -> None:
    """Store returns an empty list for an unregistered entity."""
    assert graph.find("does_not_exist", []) == []


def test_find_respects_limit(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [], limit=2)
    assert len(incidents) == 2


# ---------------------------------------------------------------------------
# display_name_of
# ---------------------------------------------------------------------------


def test_display_name_of_user(graph: SchemaGraph) -> None:
    # usr001 = John Doe per the PDF.
    assert graph.display_name_of("sys_user", "usr001") == "John Doe"


def test_display_name_of_group(graph: SchemaGraph) -> None:
    assert graph.display_name_of("sys_user_group", "grp_desktop") == "Desktop Support"


def test_display_name_of_incident(graph: SchemaGraph) -> None:
    assert graph.display_name_of("incident", "a1b2c3d4") == "INC0012345"


def test_display_name_of_dangling_returns_unknown_placeholder(
    graph: SchemaGraph,
) -> None:
    name = graph.display_name_of("sys_user", "usr999")
    assert name.startswith("[Unknown")
    assert "usr999" in name


def test_display_name_of_uses_cache_when_provided(graph: SchemaGraph) -> None:
    cache: dict[tuple[str, str], Any] = {}
    a = graph.display_name_of("sys_user", "usr001", cache=cache)
    b = graph.display_name_of("sys_user", "usr001", cache=cache)
    assert a == b == "John Doe"
    assert ("sys_user", "usr001") in cache


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


def test_aggregate_count(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [])
    result = graph.aggregate(incidents, "count")
    assert result == {"count": len(incidents)}


def test_aggregate_count_distinct(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [])
    result = graph.aggregate(
        incidents, "count_distinct", group_by_field="category",
    )
    assert result["count_distinct"] >= 4


def test_aggregate_count_distinct_without_field_returns_zero(
    graph: SchemaGraph,
) -> None:
    incidents = graph.find("incident", [])
    result = graph.aggregate(incidents, "count_distinct")
    assert result == {"count_distinct": 0}


def test_aggregate_group_by_count_resolves_reference_keys(
    graph: SchemaGraph,
) -> None:
    """Grouping incidents by assignment_group should produce display
    names like 'Desktop Support', NOT raw sys_ids like 'grp_desktop'."""
    incidents = graph.find("incident", [])
    result = graph.aggregate(
        incidents, "group_by_count",
        group_by_field="assignment_group",
        source_entity="incident",
    )
    assert "groups" in result
    assert result["groups"], "expected at least one group"
    for entry in result["groups"]:
        # Display names from the data.
        assert isinstance(entry["key"], str)
        assert not entry["key"].startswith("grp_"), (
            f"reference key {entry['key']!r} was not resolved to a display name"
        )
        # raw key preserved for audit.
        assert "key_raw" in entry
        assert entry["key_raw"].startswith("grp_")


def test_aggregate_group_by_count_resolves_value_map_keys(
    graph: SchemaGraph,
) -> None:
    """Grouping incidents by priority (value-mapped int) should produce
    'Critical' / 'High' etc., with key_raw=1/2 for audit."""
    incidents = graph.find("incident", [])
    result = graph.aggregate(
        incidents, "group_by_count",
        group_by_field="priority",
        source_entity="incident",
    )
    labels = {entry["key"] for entry in result["groups"]}
    assert labels.issubset({"Critical", "High", "Medium", "Low", "Planning"})


def test_aggregate_top_n_caps_results(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [])
    result = graph.aggregate(
        incidents, "top_n",
        group_by_field="category",
        source_entity="incident",
        n=1,
    )
    assert len(result["groups"]) == 1


def test_aggregate_group_by_skips_null_keys(graph: SchemaGraph) -> None:
    """An incident whose category is None must not produce a group with
    key=None — the validator + planner won't generate this case but the
    aggregator must be defensive."""
    fake = [
        {"sys_id": "x", "category": None},
        {"sys_id": "y", "category": "Network"},
    ]
    result = graph.aggregate(
        fake, "group_by_count",
        group_by_field="category",
    )
    assert all(g["key"] is not None for g in result["groups"])


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_projects_named_fields(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [])
    outcome = graph.resolve(
        incidents, "incident",
        fields=["number", "state", "priority"],
    )
    assert isinstance(outcome, ResolveOutcome)
    assert outcome.records
    for r in outcome.records:
        assert set(r.keys()) <= {"number", "state", "priority", "_score"}


def test_resolve_applies_value_maps(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [])
    outcome = graph.resolve(
        incidents, "incident",
        fields=["state", "priority"],
        apply_value_maps=True,
    )
    for r in outcome.records:
        assert isinstance(r["state"], str), "state should be a display label"
        assert r["state"] in {"New", "In Progress", "On Hold", "Resolved", "Closed"}


def test_resolve_includes_relations_as_display_names(
    graph: SchemaGraph,
) -> None:
    incidents = graph.find("incident", [])
    outcome = graph.resolve(
        incidents, "incident",
        fields=["number"],
        include_relations=["reportedBy"],
    )
    assert "incident.reportedBy" in outcome.relations_used
    # The synthetic dangling incident has caller_id=usr999.
    # Real incidents resolve to real user names.
    names = {r.get("reportedBy") for r in outcome.records}
    assert any(n == "John Doe" for n in names)


def test_resolve_flags_dangling_reference_in_warnings(
    graph: SchemaGraph,
) -> None:
    """INC0012350 in the fixture has caller_id=usr999 (dangling). resolve
    must produce [Unknown ...] in the output and append a warning."""
    inc = [r for r in graph.find("incident", []) if r["number"] == "INC0012350"]
    assert inc, "the dangling-ref fixture incident must exist"
    outcome = graph.resolve(
        inc, "incident",
        fields=["number"],
        include_relations=["reportedBy"],
    )
    assert outcome.records[0]["reportedBy"].startswith("[Unknown")
    assert outcome.warnings
    assert "dangling reference" in outcome.warnings[0]


def test_resolve_accepts_fully_qualified_relation_id(
    graph: SchemaGraph,
) -> None:
    incidents = graph.find("incident", [], limit=1)
    outcome = graph.resolve(
        incidents, "incident",
        fields=["number"],
        include_relations=["incident.reportedBy"],
    )
    assert "incident.reportedBy" in outcome.relations_used


def test_resolve_silently_skips_unknown_relation(graph: SchemaGraph) -> None:
    incidents = graph.find("incident", [], limit=1)
    outcome = graph.resolve(
        incidents, "incident",
        fields=["number"],
        include_relations=["notARealRelation"],
    )
    # No exception; nothing added to relations_used.
    assert outcome.relations_used == []


def test_resolve_passes_through_kb_score(graph: SchemaGraph) -> None:
    """KB records carry _score for relevance display. resolve must echo it."""
    records = graph.kb_search("VPN", top_k=2)
    outcome = graph.resolve(
        records, "kb_knowledge",
        fields=["number", "short_description"],
    )
    for r in outcome.records:
        assert "_score" in r


# ---------------------------------------------------------------------------
# kb_search
# ---------------------------------------------------------------------------


def test_kb_search_returns_articles(graph: SchemaGraph) -> None:
    results = graph.kb_search("VPN connectivity", top_k=3)
    assert results
    assert any("VPN" in r["short_description"] for r in results)


def test_kb_search_no_kb_bound_returns_empty() -> None:
    """A graph with store but no KB retriever should return [] cleanly."""
    g = load(REAL_SCHEMA)
    store = InMemoryStore(g, DATA_DIR)
    g.bind_runtime(store=store, kb=None)
    assert g.kb_search("anything") == []


def test_kb_search_respects_category_hint(graph: SchemaGraph) -> None:
    results = graph.kb_search("how to fix", category_hint="Network", top_k=5)
    for r in results:
        assert r["kb_category"].lower() == "network"


# ---------------------------------------------------------------------------
# propose_write
# ---------------------------------------------------------------------------


def test_propose_write_update_builds_diff_with_display_old_value(
    graph: SchemaGraph,
) -> None:
    inc = graph.find(
        "incident",
        [Filter(field="number", operator="eq", value="INC0012345")],
    )[0]
    action: WriteAction = "update_incident"
    proposal = graph.propose_write(
        action,
        target_record=inc,
        fields={"state": "Closed"},
        source_entity="incident",
    )
    assert proposal.action == "update_incident"
    assert proposal.target_sys_id == inc["sys_id"]
    assert proposal.target_display == "INC0012345"
    # Old value translated via value_map (int 2 -> "In Progress").
    assert proposal.diff["state"]["from"] == "In Progress"
    assert proposal.diff["state"]["to"] == "Closed"


def test_propose_write_create_uses_short_description(
    graph: SchemaGraph,
) -> None:
    proposal = graph.propose_write(
        "create_incident",
        target_record=None,
        fields={"short_description": "Printer offline", "priority": "Medium"},
    )
    assert proposal.action == "create_incident"
    assert proposal.target_display == "Printer offline"
    assert proposal.target_sys_id is None
    assert proposal.diff == {
        "short_description": {"from": None, "to": "Printer offline"},
        "priority": {"from": None, "to": "Medium"},
    }


def test_propose_write_update_without_target_raises(
    graph: SchemaGraph,
) -> None:
    with pytest.raises(ValueError):
        graph.propose_write(
            "update_incident",
            target_record=None,
            fields={"state": "Closed"},
        )


# ---------------------------------------------------------------------------
# AggOp / aliases
# ---------------------------------------------------------------------------


def test_agg_op_literal_accepted_at_call_site(graph: SchemaGraph) -> None:
    op: AggOp = "count"
    assert graph.aggregate([], op) == {"count": 0}


# ---------------------------------------------------------------------------
# Runtime-binding guard
# ---------------------------------------------------------------------------


def test_execution_methods_require_runtime_bound() -> None:
    g = load(REAL_SCHEMA)
    # Not bound: find should raise via the store property.
    with pytest.raises(RuntimeError):
        g.find("incident", [])
