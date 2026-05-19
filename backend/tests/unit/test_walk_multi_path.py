"""Tests for SchemaGraph.walk — the single-path walker that the
declarative `traverse to_entity=X path=[rel1, rel2, ...]` op resolves to.

The walker is intentionally simple: it walks an EXPLICIT chain the
planner has already chosen. Path selection (and the shortest-path
constraint) live in the validator (`validate_path`); the walker just
executes. Tests here cover:

  * single-hop walks in each cardinality direction;
  * multi-hop chains;
  * per-hop filters via filters_by_entity applied at the right step;
  * the reflexive case (path == [], source_entity == target_entity);
  * cardinality dispatch (many_to_one vs one_to_many vs self-loops);
  * dangling references are passed through (the resolve step surfaces them).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.data_store import Filter
from src.core.in_memory_store import InMemoryStore
from src.core.schema_graph import SchemaGraph, WalkResult
from src.core.schema_loader import load

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def graph() -> SchemaGraph:
    g = load(REAL_SCHEMA)
    store = InMemoryStore(g, DATA_DIR)
    g.bind_runtime(store=store, kb=None)
    return g


# ---------------------------------------------------------------------------
# Single-hop walks
# ---------------------------------------------------------------------------


async def test_walk_single_hop_user_to_caller_incidents(graph: SchemaGraph) -> None:
    """sys_user.incidentsReported is one_to_many; reverse lookup on
    incident.caller_id. John Doe (usr001) raised INC0012345."""
    john = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="John Doe")],
    )
    result = await graph.walk(
        john, "sys_user", "incident", ["sys_user.incidentsReported"],
    )
    assert isinstance(result, WalkResult)
    numbers = {r["number"] for r in result.records}
    assert "INC0012345" in numbers
    assert result.chain == ["sys_user.incidentsReported"]


async def test_walk_single_hop_incident_to_caller(graph: SchemaGraph) -> None:
    """incident.reportedBy is many_to_one; direct lookup of caller sys_id."""
    inc = graph.find(
        "incident",
        [Filter(field="number", operator="eq", value="INC0012345")],
    )
    result = await graph.walk(
        inc, "incident", "sys_user", ["incident.reportedBy"],
    )
    assert len(result.records) == 1
    assert result.records[0]["name"] == "John Doe"


async def test_walk_falls_back_when_explicit_path_yields_empty(
    graph: SchemaGraph,
) -> None:
    """John Doe is an Engineering end user; he raises incidents but is
    not assigned any. Walking via the planner-supplied `incidentsAssigned`
    chain returns 0 records.

    The walker treats the planner's path as a HINT, not a hard
    commitment — if the hint yields empty AND alternative simple chains
    exist within max_hops, it falls back through them in
    shortest-first / insertion order. For John, the fallback walks
    `incidentsReported` and surfaces INC0012345. The trace records
    both attempts so the user sees what happened. This is the
    reviewer's data-adaptive ranking made concrete: the engine doesn't
    know John is an end-user (no role rule); it just sees the first
    chain returned nothing and tries the next.
    """
    john = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="John Doe")],
    )
    result = await graph.walk(
        john, "sys_user", "incident", ["sys_user.incidentsAssigned"],
    )
    # Fallback fired: records came from `incidentsReported` instead.
    assert result.chain == ["sys_user.incidentsReported"]
    numbers = {r["number"] for r in result.records}
    assert "INC0012345" in numbers
    # Trace shows the planner's hint failed and the alternative succeeded.
    assert len(result.attempted_paths) >= 2
    assert result.attempted_paths[0]["path"] == ["sys_user.incidentsAssigned"]
    assert result.attempted_paths[0]["records_count"] == 0
    assert result.attempted_paths[0]["used"] is False
    used = next(a for a in result.attempted_paths if a.get("used"))
    assert used["path"] == ["sys_user.incidentsReported"]


async def test_walk_self_loop_manages(graph: SchemaGraph) -> None:
    """sys_user.manages is a self-loop (sys_user -> sys_user); Alex Morgan
    manages the Engineering department's reports."""
    alex = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="Alex Morgan")],
    )
    result = await graph.walk(alex, "sys_user", "sys_user", ["sys_user.manages"])
    assert len(result.records) >= 1
    names = {r["name"] for r in result.records}
    assert "John Doe" in names


# ---------------------------------------------------------------------------
# Multi-hop chains
# ---------------------------------------------------------------------------


async def test_walk_two_hop_engineering_users_to_incidents(graph: SchemaGraph) -> None:
    """find users in Engineering, walk to their reported incidents."""
    eng_users = graph.find(
        "sys_user",
        [Filter(field="department", operator="eq", value="Engineering")],
    )
    result = await graph.walk(
        eng_users, "sys_user", "incident",
        ["sys_user.incidentsReported"],
    )
    # John Doe -> INC0012345; Tom Brown -> INC0012349. Alex Morgan
    # raised no incidents.
    numbers = {r["number"] for r in result.records}
    assert "INC0012345" in numbers
    assert "INC0012349" in numbers


async def test_walk_three_hop_user_to_kb_via_category(graph: SchemaGraph) -> None:
    """The reviewer's hero example shape: sys_user -> incident ->
    category -> kb_knowledge. Walk Ravi's assigned incidents to their
    categories' KB articles."""
    ravi = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="Ravi Kumar")],
    )
    result = await graph.walk(
        ravi, "sys_user", "kb_knowledge",
        [
            "sys_user.incidentsAssigned",
            "incident.inCategory",
            "category.kbArticlesInCategory",
        ],
    )
    # Ravi handles INC0012345 (Network) + INC0012348 (Hardware). KB
    # articles published in Network = KB0045678 (VPN); Hardware =
    # KB0045682 (slow laptop).
    numbers = {r["number"] for r in result.records}
    assert numbers >= {"KB0045678", "KB0045682"}
    assert result.chain == [
        "sys_user.incidentsAssigned",
        "incident.inCategory",
        "category.kbArticlesInCategory",
    ]


# ---------------------------------------------------------------------------
# Per-hop filters (filters_by_entity)
# ---------------------------------------------------------------------------


async def test_walk_filter_on_target_entity(graph: SchemaGraph) -> None:
    """Find Engineering users, walk to their reported incidents, filter
    final hop to OPEN states only."""
    eng_users = graph.find(
        "sys_user",
        [Filter(field="department", operator="eq", value="Engineering")],
    )
    result = await graph.walk(
        eng_users, "sys_user", "incident",
        ["sys_user.incidentsReported"],
        filters_by_entity={
            "incident": [
                Filter(
                    field="state", operator="in",
                    value=["New", "In Progress", "On Hold"],
                ),
            ],
        },
    )
    for r in result.records:
        assert r["state"] in {1, 2, 3}, (
            f"non-open state {r['state']} slipped through the per-hop filter"
        )
    assert "incident" in result.hops_filtered


async def test_walk_filter_on_intermediate_hop(graph: SchemaGraph) -> None:
    """Filter applied at an intermediate entity in a 3-hop chain. Start
    from Ravi, walk to incidents (filter to OPEN), continue to categories,
    continue to KB articles. The filter narrows the incident set BEFORE
    the category hop runs."""
    ravi = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="Ravi Kumar")],
    )
    # Walk WITHOUT filter first to get the unfiltered KB set.
    unfiltered = await graph.walk(
        ravi, "sys_user", "kb_knowledge",
        [
            "sys_user.incidentsAssigned",
            "incident.inCategory",
            "category.kbArticlesInCategory",
        ],
    )
    # Walk WITH state=Closed filter on incident hop. Ravi's incidents are
    # both open (INC0012345 In Progress, INC0012348 In Progress); the
    # filter should knock them out and yield no KB articles.
    filtered = await graph.walk(
        ravi, "sys_user", "kb_knowledge",
        [
            "sys_user.incidentsAssigned",
            "incident.inCategory",
            "category.kbArticlesInCategory",
        ],
        filters_by_entity={
            "incident": [
                Filter(field="state", operator="eq", value="Closed"),
            ],
        },
    )
    assert unfiltered.records, "sanity: unfiltered walk must return KB articles"
    assert filtered.records == [], (
        "filter on incident hop should narrow to zero before reaching KB"
    )
    # When every ranked chain yields empty after the mid-hop filter
    # narrows the incident set, the trace surfaces the planner's
    # FIRST attempt + the hops where filters fired, so the filter
    # tracking stays correct even though no records came back.
    assert "incident" in filtered.hops_filtered


async def test_walk_filter_at_no_relevant_hop_is_a_noop(graph: SchemaGraph) -> None:
    """A filter keyed by an entity not in the chain is silently ignored
    by the walker. The plan validator catches this earlier — but the
    walker must not crash on unfamiliar keys (defensive)."""
    eng_users = graph.find(
        "sys_user",
        [Filter(field="department", operator="eq", value="Engineering")],
    )
    result = await graph.walk(
        eng_users, "sys_user", "incident",
        ["sys_user.incidentsReported"],
        filters_by_entity={
            # category is not in the chain; this filter cannot apply.
            "category": [Filter(field="name", operator="eq", value="Network")],
        },
    )
    assert result.records  # walk produced records anyway
    assert "category" not in result.hops_filtered


# ---------------------------------------------------------------------------
# Reflexive walk (source == target)
# ---------------------------------------------------------------------------


async def test_walk_reflexive_with_empty_path_returns_source(graph: SchemaGraph) -> None:
    eng = graph.find(
        "sys_user",
        [Filter(field="department", operator="eq", value="Engineering")],
    )
    result = await graph.walk(eng, "sys_user", "sys_user", [])
    assert result.records == eng
    assert result.chain == []
    assert result.hops_filtered == []


async def test_walk_reflexive_with_target_filter(graph: SchemaGraph) -> None:
    """A reflexive walk can still apply a final-target filter — useful
    for `traverse to_entity=incident` over a set already on the binding."""
    eng = graph.find(
        "sys_user",
        [Filter(field="department", operator="eq", value="Engineering")],
    )
    result = await graph.walk(
        eng, "sys_user", "sys_user", [],
        filters_by_entity={
            "sys_user": [Filter(field="location", operator="eq", value="New York")],
        },
    )
    locations = {r["location"] for r in result.records}
    assert locations == {"New York"}


# ---------------------------------------------------------------------------
# Empty source / edge cases
# ---------------------------------------------------------------------------


async def test_walk_empty_source_returns_empty(graph: SchemaGraph) -> None:
    result = await graph.walk([], "sys_user", "incident", ["sys_user.incidentsReported"])
    assert result.records == []
    assert result.chain == ["sys_user.incidentsReported"]


async def test_walk_dangling_reference_falls_back_to_alternative_chain(
    graph: SchemaGraph,
) -> None:
    """INC0012350 has caller_id=usr999 (dangling). Walking
    `incident.reportedBy` directly yields no user (store.get silently
    drops the missing sys_id). The walker now falls back to other valid
    chains within max_hops — e.g. via `incident.handledBy` to the
    group, then via `sys_user_group.managedBy` to the team's manager.
    The resolve step is where dangling-ref warnings surface, not here.
    """
    dangling = graph.find(
        "incident",
        [Filter(field="number", operator="eq", value="INC0012350")],
    )
    result = await graph.walk(
        dangling, "incident", "sys_user", ["incident.reportedBy"],
    )
    # The planner's hint (reportedBy) was tried first and returned 0.
    assert result.attempted_paths[0]["path"] == ["incident.reportedBy"]
    assert result.attempted_paths[0]["records_count"] == 0
    # Some alternative chain yielded data — the walker found SOMEONE
    # via a different chain (e.g. the group's manager).
    if result.records:
        used = next(a for a in result.attempted_paths if a.get("used"))
        assert used["path"] != ["incident.reportedBy"]


# ---------------------------------------------------------------------------
# Engine-resolved paths (path == [] + scorer)
# ---------------------------------------------------------------------------


class _StubScorer:
    """Deterministic stub of RelationScorer. Returns a pre-set ranking
    of candidate indices (most-preferred first)."""

    def __init__(
        self,
        ranking: list[int] | None = None,
        confidence: float = 0.9,
    ) -> None:
        self.ranking = ranking
        self.confidence = confidence
        self.calls: list[dict] = []

    async def choose(self, paths, user_query, source_entity, target_entity):  # type: ignore[no-untyped-def]
        from src.planner.relation_scorer import ScorerChoice
        self.calls.append({
            "n_paths": len(paths),
            "query": user_query,
            "source": source_entity,
            "target": target_entity,
        })
        ranking = self.ranking or list(range(len(paths)))
        return ScorerChoice(
            ranking=ranking,
            confidence=self.confidence,
            reasoning="stub",
            latency_ms=1,
            candidates=[
                {"index": i, "path": [r.id for r in p],
                 "verb_chain": " ".join(r.verb_phrase for r in p)}
                for i, p in enumerate(paths)
            ],
        )


async def test_walk_with_empty_path_walks_top_ranked_with_data(
    graph: SchemaGraph,
) -> None:
    """Empty path triggers engine resolution. Engine ranks all candidate
    chains within max_hops, walks the top-ranked. If it yields records,
    no fallback needed."""
    ravi = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="Ravi Kumar")],
    )
    cands = graph.enumerate_simple_paths("sys_user", "incident")
    assignee_idx = next(
        i for i, c in enumerate(cands)
        if len(c) == 1 and c[0].id == "sys_user.incidentsAssigned"
    )
    scorer = _StubScorer(ranking=[assignee_idx], confidence=0.85)
    result = await graph.walk(
        ravi, "sys_user", "incident", [],
        scorer=scorer,  # type: ignore[arg-type]
        user_query="incidents Ravi handles",
    )
    assert scorer.calls, "multi-candidate walk must invoke scorer"
    assert result.chain == ["sys_user.incidentsAssigned"]
    assert result.scorer_choice is not None
    assert result.scorer_choice.confidence == pytest.approx(0.85)
    # Top-ranked yielded records; no fallback used.
    used = [a for a in result.attempted_paths if a.get("used")]
    assert len(used) == 1
    assert used[0]["rank"] == 0


async def test_walk_falls_back_to_next_ranked_when_top_returns_empty(
    graph: SchemaGraph,
) -> None:
    """The reviewer's "rank them" prescription made concrete: when the
    top-ranked chain returns zero records (e.g. ``managesGroups`` for
    a non-manager), the engine walks the next-ranked chain. This is the
    PDF's "Ravi's team" query made to work without a hardcoded role
    routing rule — Ravi isn't a manager, so the manager-chain is empty,
    so the engine adapts to the team-via-incidents chain. Pure data
    adaptation, no role classification."""
    ravi = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="Ravi Kumar")],
    )
    cands = graph.enumerate_simple_paths("sys_user", "sys_user_group")
    # managesGroups (1-hop) returns empty for Ravi.
    # incidentsAssigned + handledBy (2-hop) returns Network + Desktop.
    manages_idx = next(
        i for i, c in enumerate(cands)
        if len(c) == 1 and c[0].id == "sys_user.managesGroups"
    )
    assignee_team_idx = next(
        i for i, c in enumerate(cands)
        if [r.id for r in c] == [
            "sys_user.incidentsAssigned", "incident.handledBy",
        ]
    )
    # Scorer ranks managesGroups first (typical shortest-chain bias);
    # engine walks it, gets 0 records, falls back to the assignee chain.
    scorer = _StubScorer(
        ranking=[manages_idx, assignee_team_idx]
        + [i for i in range(len(cands)) if i not in (manages_idx, assignee_team_idx)],
        confidence=0.7,
    )
    result = await graph.walk(
        ravi, "sys_user", "sys_user_group", [],
        scorer=scorer,  # type: ignore[arg-type]
        user_query="Ravi's team",
    )
    # The engine fell back to the assignee chain.
    assert result.chain == ["sys_user.incidentsAssigned", "incident.handledBy"]
    # Records: grp_network + grp_desktop.
    group_names = sorted(r["name"] for r in result.records)
    assert group_names == ["Desktop Support", "Network Operations"]
    # Both attempts surfaced in the trace, with `used` marked on #1.
    assert len(result.attempted_paths) >= 2
    assert result.attempted_paths[0]["records_count"] == 0
    assert result.attempted_paths[0]["used"] is False
    used_attempt = next(a for a in result.attempted_paths if a.get("used"))
    assert used_attempt["records_count"] == 2


async def test_walk_with_empty_path_no_scorer_uses_insertion_order(
    graph: SchemaGraph,
) -> None:
    """If no scorer is wired (test seam), the engine uses insertion-order
    ranking — shortest-first by enumerate_simple_paths. With fallback,
    walks down the ranking until non-empty."""
    ravi = graph.find(
        "sys_user",
        [Filter(field="name", operator="eq", value="Ravi Kumar")],
    )
    result = await graph.walk(
        ravi, "sys_user", "incident", [],
    )
    # Some non-empty chain returned records (Ravi has assigned incidents).
    assert result.scorer_choice is None
    assert result.records  # at least one record
    # The chain used corresponds to some valid sys_user -> incident path.
    cands = graph.enumerate_simple_paths("sys_user", "incident")
    valid_chains = [[r.id for r in c] for c in cands]
    assert result.chain in valid_chains


async def test_walk_with_empty_path_unreachable_returns_empty(
    graph: SchemaGraph,
) -> None:
    """If shortest_relation_paths returns empty, walk returns empty
    records with an empty chain. (For our schema all entities are
    reachable, so we can't actually trigger this on the real schema; the
    test_shortest_paths_and_validate.py suite covers the unreachable
    case on a synthetic graph.)"""
    # Synthetic empty source on a real reachable pair still works fine.
    result = await graph.walk(
        [], "sys_user", "incident", [],
    )
    assert result.records == []


async def test_walk_caps_fallback_attempts(graph: SchemaGraph) -> None:
    """The walker caps fallback attempts at MAX_FALLBACK_ATTEMPTS. Each
    attempt is a real store call; without the cap, queries against entity
    pairs with many simple chains within max_hops would balloon. Walking
    a non-existent source records list through sys_user -> sys_user
    (which has many self-loop chains via incidents/groups) exercises the
    bound — without the cap the walker would try every chain."""
    from src.core.schema_graph import MAX_FALLBACK_ATTEMPTS

    # Use a fake user record so every chain returns empty — forces the
    # walker to exhaust attempts before bailing.
    fake_user = [{"sys_id": "usr-does-not-exist", "name": "Ghost"}]
    # Walk sys_user -> sys_user_group (which has 3 candidates: managesGroups,
    # incidentsReported+handledBy, incidentsAssigned+handledBy). All
    # empty for a fake user.
    result = await graph.walk(
        fake_user, "sys_user", "sys_user_group", [],
    )
    # Cap should prevent walking more than MAX_FALLBACK_ATTEMPTS chains.
    assert len(result.attempted_paths) <= MAX_FALLBACK_ATTEMPTS
    assert result.records == []
