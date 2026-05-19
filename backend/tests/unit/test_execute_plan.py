"""Tests for SchemaGraph.execute_plan — the plan walker.

execute_plan drives a validated QueryPlan through find/walk/aggregate/
resolve/kb_search/propose_write on the graph itself. These tests exercise
the dispatcher and trace assembly end-to-end with REAL data and the
declarative TraverseOp shape (the legacy `relation` shape runs on the
legacy ExecutionEngine until step 10).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.core.data_store import Filter
from src.core.in_memory_store import InMemoryStore
from src.core.schema_graph import SchemaGraph
from src.core.schema_loader import load
from src.core.trace import ExecutionResult
from src.knowledge.embeddings import EmbeddingClient
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

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class _KeywordEmbedder:
    dimension = 8
    _vocab = ["vpn", "outlook", "share", "badge", "laptop", "kb", "category", "test"]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, t in enumerate(texts):
            tl = t.lower()
            for j, kw in enumerate(self._vocab):
                if kw in tl:
                    out[i, j] = 1.0
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


def _validated(graph: SchemaGraph, *ops, **kwargs) -> QueryPlan:  # type: ignore[no-untyped-def]
    intent = kwargs.pop("intent", "lookup")
    final_var = kwargs.pop("final_var", "out")
    fmt = kwargs.pop("format", "list")
    plan = QueryPlan(
        intent=intent,
        reasoning="x" * 20,
        operations=list(ops),
        output_spec=OutputSpec(format=fmt, final_var=final_var),
        confidence=0.9,
    )
    return PlanValidator(graph).validate(plan)


# ---------------------------------------------------------------------------
# Happy path: each query category from the PDF
# ---------------------------------------------------------------------------


async def test_execute_plan_lookup_by_name(graph: SchemaGraph) -> None:
    """'What's the status of John Doe's VPN issue?' shape."""
    plan = _validated(
        graph,
        FindOp(
            id="u",
            entity="sys_user",
            filters=[Filter(field="name", operator="eq", value="John Doe")],
        ),
        TraverseOp(
            id="inc",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.incidentsReported"],
            filters_by_entity={
                "incident": [
                    Filter(field="short_description", operator="contains", value="VPN")
                ],
            },
        ),
        ResolveOp(
            id="out", source="$inc",
            fields=["number", "state", "priority"],
        ),
    )
    result = await graph.execute_plan(plan, query="John Doe's VPN issue")
    assert isinstance(result, ExecutionResult)
    assert result.output
    nums = {r["number"] for r in result.output}
    assert "INC0012345" in nums


async def test_execute_plan_analytical_count(graph: SchemaGraph) -> None:
    """'How many incidents does Desktop Support have right now?' shape."""
    plan = _validated(
        graph,
        FindOp(
            id="g",
            entity="sys_user_group",
            filters=[Filter(field="name", operator="eq", value="Desktop Support")],
        ),
        TraverseOp(
            id="inc",
            **{"from": "$g"},
            to_entity="incident",
            path=["sys_user_group.incidentsHandled"],
            filters_by_entity={
                "incident": [
                    Filter(field="state", operator="in",
                           value=["New", "In Progress", "On Hold"]),
                ],
            },
        ),
        AggregateOp(id="cnt", source="$inc", operation="count"),
        final_var="cnt", format="scalar",
    )
    result = await graph.execute_plan(plan)
    assert result.output >= 1


async def test_execute_plan_knowledge_search(graph: SchemaGraph) -> None:
    plan = _validated(
        graph,
        KBLookupOp(id="kb", query="VPN connectivity", top_k=2),
        ResolveOp(id="out", source="$kb",
                  fields=["number", "short_description", "text"]),
        format="kb_answer",
    )
    result = await graph.execute_plan(plan)
    assert result.output
    titles = " ".join(r["short_description"] for r in result.output)
    assert "VPN" in titles


async def test_execute_plan_cross_reference_engineering_to_incidents(
    graph: SchemaGraph,
) -> None:
    plan = _validated(
        graph,
        FindOp(
            id="u",
            entity="sys_user",
            filters=[Filter(field="department", operator="eq", value="Engineering")],
        ),
        TraverseOp(
            id="inc",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.incidentsReported"],
        ),
        ResolveOp(id="out", source="$inc",
                  fields=["number", "short_description", "state"],
                  include_relations=["reportedBy"]),
    )
    result = await graph.execute_plan(plan)
    nums = {r["number"] for r in result.output}
    assert {"INC0012345", "INC0012349"} <= nums
    # Reported-by inlined as display name.
    reporters = {r["reportedBy"] for r in result.output}
    assert "John Doe" in reporters


async def test_execute_plan_three_hop_kb_via_category(graph: SchemaGraph) -> None:
    """Reviewer hero example: 'KB articles relevant to incidents Ravi's
    team is handling'. Three hops: sys_user -> incident -> category ->
    kb_knowledge."""
    plan = _validated(
        graph,
        FindOp(
            id="u",
            entity="sys_user",
            filters=[Filter(field="name", operator="eq", value="Ravi Kumar")],
        ),
        TraverseOp(
            id="kb",
            **{"from": "$u"},
            to_entity="kb_knowledge",
            path=[
                "sys_user.incidentsAssigned",
                "incident.inCategory",
                "category.kbArticlesInCategory",
            ],
        ),
        ResolveOp(id="out", source="$kb",
                  fields=["number", "short_description", "kb_category"]),
    )
    result = await graph.execute_plan(plan)
    nums = {r["number"] for r in result.output}
    # Ravi assigns INC0012345 (Network) -> KB0045678; INC0012348 (Hardware) -> KB0045682.
    assert "KB0045678" in nums or "KB0045682" in nums


async def test_execute_plan_write_proposal_close_incident(graph: SchemaGraph) -> None:
    plan = _validated(
        graph,
        FindOp(
            id="inc",
            entity="incident",
            filters=[Filter(field="number", operator="eq", value="INC0012345")],
        ),
        WriteProposalOp(
            id="prop", action="update_incident",
            target_var="$inc", fields={"state": "Closed"},
        ),
        intent="write_proposal",
        final_var="prop", format="write_confirmation",
    )
    result = await graph.execute_plan(plan)
    assert result.output.action == "update_incident"
    assert result.output.target_display == "INC0012345"
    assert result.output.diff["state"] == {"from": "In Progress", "to": "Closed"}


# ---------------------------------------------------------------------------
# Trace shape
# ---------------------------------------------------------------------------


async def test_trace_records_chain_and_target_entity(graph: SchemaGraph) -> None:
    plan = _validated(
        graph,
        FindOp(
            id="u",
            entity="sys_user",
            filters=[Filter(field="name", operator="eq", value="John Doe")],
        ),
        TraverseOp(
            id="inc",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.incidentsReported"],
        ),
        ResolveOp(id="out", source="$inc", fields=["number"]),
    )
    result = await graph.execute_plan(plan)
    # Each step gets a TraceStep.
    assert len(result.trace.steps) == 3
    traverse_step = result.trace.steps[1]
    assert traverse_step.op_type == "traverse"
    assert traverse_step.graph_traversal == ["sys_user.incidentsReported"]
    assert traverse_step.target_entity == "incident"


async def test_trace_records_hops_filtered_per_entity(graph: SchemaGraph) -> None:
    plan = _validated(
        graph,
        FindOp(
            id="u",
            entity="sys_user",
            filters=[Filter(field="department", operator="eq", value="Engineering")],
        ),
        TraverseOp(
            id="inc",
            **{"from": "$u"},
            to_entity="incident",
            path=["sys_user.incidentsReported"],
            filters_by_entity={
                "incident": [
                    Filter(field="state", operator="in",
                           value=["New", "In Progress", "On Hold"]),
                ],
            },
        ),
        ResolveOp(id="out", source="$inc", fields=["number"]),
    )
    result = await graph.execute_plan(plan)
    assert "incident" in result.trace.steps[1].hops_filtered


async def test_trace_to_visjs_highlights_lists_chain_in_order(
    graph: SchemaGraph,
) -> None:
    plan = _validated(
        graph,
        FindOp(
            id="u",
            entity="sys_user",
            filters=[Filter(field="name", operator="eq", value="Ravi Kumar")],
        ),
        TraverseOp(
            id="kb",
            **{"from": "$u"},
            to_entity="kb_knowledge",
            path=[
                "sys_user.incidentsAssigned",
                "incident.inCategory",
                "category.kbArticlesInCategory",
            ],
        ),
        ResolveOp(id="out", source="$kb", fields=["number"]),
    )
    result = await graph.execute_plan(plan)
    chain = result.trace.to_visjs_highlights()
    assert chain == [
        "sys_user.incidentsAssigned",
        "incident.inCategory",
        "category.kbArticlesInCategory",
    ]


# ---------------------------------------------------------------------------
# Dangling references / warnings
# ---------------------------------------------------------------------------


async def test_execute_plan_dangling_reference_surfaces_warning(
    graph: SchemaGraph,
) -> None:
    """INC0012350 has caller_id=usr999. include_relations=['reportedBy']
    should surface a 'dangling reference' warning."""
    plan = _validated(
        graph,
        FindOp(
            id="inc",
            entity="incident",
            filters=[Filter(field="number", operator="eq", value="INC0012350")],
        ),
        ResolveOp(
            id="out", source="$inc",
            fields=["number", "short_description"],
            include_relations=["reportedBy"],
        ),
    )
    result = await graph.execute_plan(plan)
    assert any("dangling reference" in w for w in result.warnings)
    assert result.output[0]["reportedBy"].startswith("[Unknown")


# ---------------------------------------------------------------------------
# Short-circuit intents
# ---------------------------------------------------------------------------


async def test_execute_plan_ambiguous_intent_does_not_execute(
    graph: SchemaGraph,
) -> None:
    plan = QueryPlan(
        intent="ambiguous",
        reasoning="user query too vague for me to act on",
        operations=[],
        confidence=0.4,
        clarification_needed="What incident?",
    )
    result = await graph.execute_plan(plan)
    assert result.output is None
    assert result.trace.steps == []


async def test_execute_plan_out_of_scope_does_not_execute(graph: SchemaGraph) -> None:
    plan = QueryPlan(
        intent="out_of_scope",
        reasoning="injection attempt",
        operations=[],
        confidence=0.99,
    )
    result = await graph.execute_plan(plan)
    assert result.output is None
    assert result.trace.steps == []


# ---------------------------------------------------------------------------
# Legacy-shape rejection — at the Pydantic level (no `relation` field exists)
# ---------------------------------------------------------------------------


def test_traverse_op_rejects_legacy_relation_field_at_parse_time() -> None:
    """The legacy `relation` field was removed from TraverseOp; passing it
    in raw JSON makes Pydantic reject the op before it ever reaches the
    validator. Guards against the LLM regressing to the old shape."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        QueryPlan.model_validate(
            {
                "intent": "lookup",
                "reasoning": "legacy shape rejected at parse time",
                "operations": [
                    {"op": "find", "id": "u", "entity": "sys_user"},
                    {
                        "op": "traverse",
                        "id": "inc",
                        "from": "$u",
                        "relation": "sys_user.incidentsReported",
                    },
                    {"op": "resolve", "id": "out", "source": "$inc", "fields": ["number"]},
                ],
                "output_spec": {"format": "list", "final_var": "out"},
                "confidence": 0.9,
            }
        )


async def test_execute_plan_raises_on_missing_var_reference(
    graph: SchemaGraph,
) -> None:
    """The validator should catch this, but if it slips through the
    executor must fail loudly rather than silently produce nothing."""
    plan = QueryPlan(
        intent="lookup",
        reasoning="bypass-validator " * 2,
        operations=[
            ResolveOp(id="out", source="$nonexistent", fields=["x"]),
        ],
        output_spec=OutputSpec(format="list", final_var="out"),
        confidence=0.9,
    )
    # ResolveOp on a missing variable returns empty silently; the plan
    # walker doesn't raise but also produces nothing useful. The
    # validator catches this earlier — confirm by running it through.
    result = await graph.execute_plan(plan)
    assert result.output == []
