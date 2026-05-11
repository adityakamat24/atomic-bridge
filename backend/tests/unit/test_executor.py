from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.core.data_store import Filter
from src.core.in_memory_store import InMemoryStore
from src.core.schema_loader import load
from src.executor.engine import ExecutionEngine, ExecutionError, ExecutionResult
from src.executor.reference_resolver import ReferenceResolver
from src.knowledge.indexer import KBIndexer
from src.knowledge.retriever import KBRetriever
from src.planner.plan_schema import (
    AggregateOp,
    FindOp,
    KBLookupOp,
    OutputSpec,
    QueryPlan,
    ResolveOp,
    TraverseOp,
    WriteProposalOp,
)
from src.planner.plan_validator import PlanValidator
from src.write_path.proposal import WriteProposal

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# ---------- Fixtures --------------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(REPO_DATA_DIR, target)
    return target


@pytest.fixture
def engine(isolated_data_dir: Path) -> ExecutionEngine:
    g = load(isolated_data_dir / "schema.yaml")
    store = InMemoryStore(g, isolated_data_dir)
    return ExecutionEngine(g, store)


@pytest.fixture
def engine_with_kb(isolated_data_dir: Path) -> ExecutionEngine:
    """Uses the deterministic KeywordEmbeddingClient so we can call kb_lookup
    without paying the sentence-transformers cost."""
    from tests.unit.test_kb_retriever import KeywordEmbeddingClient

    g = load(isolated_data_dir / "schema.yaml")
    store = InMemoryStore(g, isolated_data_dir)
    kb = KBRetriever(store, KBIndexer(KeywordEmbeddingClient()))
    kb.build_index()
    return ExecutionEngine(g, store, kb=kb)


def _validate(g, plan: QueryPlan) -> QueryPlan:
    return PlanValidator(g).validate(plan)


# ---------- Find handler ----------------------------------------------------


def test_find_in_progress_returns_value_mapped_results(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="lookup",
        reasoning="find the in-progress incidents",
        operations=[
            FindOp(
                id="x",
                entity="incident",
                filters=[Filter(field="state", operator="eq", value="In Progress")],
            ),
            ResolveOp(id="out", source="$x", fields=["number", "state", "priority"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    assert isinstance(result, ExecutionResult)
    assert len(result.output) == 3
    states = {r["state"] for r in result.output}
    assert states == {"In Progress"}


# ---------- Multi-hop traverse ---------------------------------------------


def test_multi_hop_engineering_to_incidents(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="cross_reference",
        reasoning="find Engineering users then their incidents",
        operations=[
            FindOp(
                id="u",
                entity="sys_user",
                filters=[Filter(field="department", operator="eq", value="Engineering")],
            ),
            TraverseOp.model_validate(
                {
                    "op": "traverse",
                    "id": "i",
                    "from": "$u",
                    "relation": "sys_user.incidentsReported",
                }
            ),
            ResolveOp(id="out", source="$i", fields=["number", "state"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    numbers = {r["number"] for r in result.output}
    assert {"INC0012345", "INC0012349"} <= numbers


def test_three_hop_manager_of_vpn_reporter(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    """John Doe (caller of INC0012345 = VPN) is managed by Alex Morgan."""
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="cross_reference",
        reasoning="find vpn incident, traverse to caller, traverse to manager",
        operations=[
            FindOp(
                id="inc",
                entity="incident",
                filters=[
                    Filter(field="short_description", operator="contains", value="VPN")
                ],
            ),
            TraverseOp.model_validate(
                {
                    "op": "traverse",
                    "id": "rep",
                    "from": "$inc",
                    "relation": "incident.reportedBy",
                }
            ),
            TraverseOp.model_validate(
                {
                    "op": "traverse",
                    "id": "mgr",
                    "from": "$rep",
                    "relation": "sys_user.managedBy",
                }
            ),
            ResolveOp(id="out", source="$mgr", fields=["name"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    names = {r["name"] for r in result.output}
    assert "Alex Morgan" in names


# ---------- Aggregate handler -----------------------------------------------


def test_count_critical_priority_is_one(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="analytical",
        reasoning="count critical priority incidents",
        operations=[
            FindOp(
                id="x",
                entity="incident",
                filters=[Filter(field="priority", operator="eq", value="Critical")],
            ),
            AggregateOp(id="cnt", source="$x", operation="count"),
        ],
        output_spec=OutputSpec(format="scalar", final_var="cnt"),
        confidence=0.95,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    assert result.output == 1


def test_group_by_count_per_category(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="analytical",
        reasoning="open incidents per category",
        operations=[
            FindOp(
                id="x",
                entity="incident",
                filters=[
                    Filter(
                        field="state",
                        operator="in",
                        value=["New", "In Progress", "On Hold"],
                    )
                ],
            ),
            AggregateOp(
                id="g",
                source="$x",
                operation="group_by_count",
                group_by_field="category",
            ),
        ],
        output_spec=OutputSpec(format="scalar", final_var="g"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    groups = result.output["groups"]
    keys = {g["key"] for g in groups}
    assert {"Network", "Software"} <= keys
    # Plain-string field: no translation, no `key_raw` echoed.
    assert all("key_raw" not in g for g in groups)


def test_group_by_count_on_reference_field_resolves_display_names(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    """Grouping by `assignment_group` must surface 'Cloud Infrastructure',
    not the raw sys_id 'grp_cloud'. Raw key preserved as `key_raw`."""
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="analytical",
        reasoning="workload per team",
        operations=[
            FindOp(id="x", entity="incident"),
            AggregateOp(
                id="g",
                source="$x",
                operation="top_n",
                group_by_field="assignment_group",
                n=5,
            ),
        ],
        output_spec=OutputSpec(format="scalar", final_var="g"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    groups = result.output["groups"]
    keys = {grp["key"] for grp in groups}
    raw_keys = {grp["key_raw"] for grp in groups if "key_raw" in grp}
    # Display names surface as the primary key.
    assert "Cloud Infrastructure" in keys or "Desktop Support" in keys
    # No sys_id-shaped strings leak as the primary `key`.
    assert not any(isinstance(k, str) and k.startswith("grp_") for k in keys)
    # Raw sys_id is preserved for audit on every translated entry.
    assert any(rk.startswith("grp_") for rk in raw_keys)


def test_group_by_count_on_value_mapped_field_resolves_labels(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    """Grouping by `priority` must surface 'High'/'Critical', not 1/2."""
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="analytical",
        reasoning="incidents per priority",
        operations=[
            FindOp(id="x", entity="incident"),
            AggregateOp(
                id="g",
                source="$x",
                operation="group_by_count",
                group_by_field="priority",
            ),
        ],
        output_spec=OutputSpec(format="scalar", final_var="g"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    groups = result.output["groups"]
    keys = {grp["key"] for grp in groups}
    assert keys & {"Critical", "High", "Medium", "Low", "Planning"}
    assert not any(isinstance(k, int) for k in keys)


# ---------- Resolve handler -------------------------------------------------


def test_resolve_strips_sys_id_by_default(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="lookup",
        reasoning="get an incident",
        operations=[
            FindOp(
                id="x",
                entity="incident",
                filters=[Filter(field="number", operator="eq", value="INC0012345")],
            ),
            ResolveOp(id="out", source="$x", fields=["number", "state"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    rec = result.output[0]
    assert "sys_id" not in rec
    assert rec["state"] == "In Progress"


def test_resolve_inlines_relation_display_names(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="lookup",
        reasoning="resolve with reportedBy inlined",
        operations=[
            FindOp(
                id="x",
                entity="incident",
                filters=[Filter(field="number", operator="eq", value="INC0012345")],
            ),
            ResolveOp(
                id="out",
                source="$x",
                fields=["number", "state"],
                include_relations=["reportedBy", "assignedTo"],
            ),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    rec = result.output[0]
    assert rec["reportedBy"] == "John Doe"
    assert rec["assignedTo"] == "Ravi Kumar"


# ---------- Dangling reference --------------------------------------------


def test_dangling_reference_produces_unknown_placeholder(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    """INC0012350 has caller_id=usr999 (deleted user fixture)."""
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="lookup",
        reasoning="get the dangling-reference incident",
        operations=[
            FindOp(
                id="x",
                entity="incident",
                filters=[Filter(field="number", operator="eq", value="INC0012350")],
            ),
            ResolveOp(
                id="out",
                source="$x",
                fields=["number", "state"],
                include_relations=["reportedBy"],
            ),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    rec = result.output[0]
    assert rec["reportedBy"].startswith("[Unknown")
    assert any("dangling" in w for w in result.warnings)


def test_reference_resolver_caches_lookups(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    resolver = ReferenceResolver(g, InMemoryStore(g, isolated_data_dir))
    a = resolver.resolve("sys_user", "usr001")
    b = resolver.resolve("sys_user", "usr001")
    assert a is not None
    assert b is not None
    assert a == b
    assert resolver.cache_size() == 1


# ---------- KB lookup -------------------------------------------------------


def test_kb_lookup_returns_articles(
    engine_with_kb: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="knowledge",
        reasoning="kb lookup for vpn",
        operations=[
            KBLookupOp(id="kb", query="vpn", category_hint="Network", top_k=2),
            ResolveOp(
                id="out", source="$kb", fields=["number", "short_description"]
            ),
        ],
        output_spec=OutputSpec(format="kb_answer", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine_with_kb.execute(plan)
    assert result.output[0]["number"] == "KB0045678"


# ---------- Write proposal --------------------------------------------------


def test_write_proposal_builds_diff(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="write_proposal",
        reasoning="close the vpn incident",
        operations=[
            FindOp(
                id="inc",
                entity="incident",
                filters=[Filter(field="number", operator="eq", value="INC0012345")],
            ),
            WriteProposalOp(
                id="prop",
                action="update_incident",
                target_var="$inc",
                fields={"state": "Closed"},
            ),
        ],
        output_spec=OutputSpec(format="write_confirmation", final_var="prop"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    proposal = result.output
    assert isinstance(proposal, WriteProposal)
    assert proposal.diff["state"]["from"] == "In Progress"
    assert proposal.diff["state"]["to"] == "Closed"
    assert proposal.target_display == "INC0012345"


def test_write_proposal_create_has_full_field_diff(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="write_proposal",
        reasoning="create new",
        operations=[
            WriteProposalOp(
                id="prop",
                action="create_incident",
                fields={
                    "short_description": "Printer offline",
                    "priority": "High",
                    "category": "Hardware",
                },
            ),
        ],
        output_spec=OutputSpec(format="write_confirmation", final_var="prop"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    proposal = result.output
    assert proposal.diff["short_description"]["from"] is None
    assert proposal.diff["priority"]["to"] == "High"


# ---------- Trace -----------------------------------------------------------


def test_trace_records_each_step(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="lookup",
        reasoning="trace test",
        operations=[
            FindOp(id="x", entity="incident"),
            ResolveOp(id="out", source="$x", fields=["number"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    assert len(result.trace.steps) == 2
    assert {s.op_type for s in result.trace.steps} == {"find", "resolve"}
    assert all(s.latency_ms >= 0 for s in result.trace.steps)
    assert result.trace.total_latency_ms >= 0


def test_trace_to_visjs_highlights_lists_traversed_relations(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    g = load(isolated_data_dir / "schema.yaml")
    plan = QueryPlan(
        intent="cross_reference",
        reasoning="touch a relation",
        operations=[
            FindOp(
                id="u",
                entity="sys_user",
                filters=[Filter(field="department", operator="eq", value="Engineering")],
            ),
            TraverseOp.model_validate(
                {
                    "op": "traverse",
                    "id": "i",
                    "from": "$u",
                    "relation": "sys_user.incidentsReported",
                }
            ),
            ResolveOp(id="out", source="$i", fields=["number"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    plan = _validate(g, plan)
    result = engine.execute(plan)
    assert "sys_user.incidentsReported" in result.trace.to_visjs_highlights()


# ---------- Terminal intents ------------------------------------------------


def test_ambiguous_plan_returns_no_output(
    engine: ExecutionEngine,
) -> None:
    plan = QueryPlan(
        intent="ambiguous",
        reasoning="too broad to action automatically",
        operations=[],
        confidence=0.4,
        clarification_needed="What did you mean?",
    )
    result = engine.execute(plan)
    assert result.output is None
    assert result.trace.steps == []


def test_undefined_final_var_raises(
    engine: ExecutionEngine, isolated_data_dir: Path
) -> None:
    # Build a plan that bypasses validator output_spec check by stuffing a
    # non-existent final_var directly. This simulates a corrupted plan slipping
    # past the validator.
    plan = QueryPlan(
        intent="lookup",
        reasoning="bad final_var",
        operations=[FindOp(id="x", entity="incident")],
        output_spec=OutputSpec(format="list", final_var="ghost"),
        confidence=0.9,
    )
    # Don't call validator; plan validator would normally catch this.
    with pytest.raises(ExecutionError):
        engine.execute(plan)
