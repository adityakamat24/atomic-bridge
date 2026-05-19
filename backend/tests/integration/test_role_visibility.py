"""End-to-end role-visibility tests driving planner -> validator -> executor
with a scripted LLM, plus PII stripping and validator-rejection cases."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from src.core.in_memory_store import InMemoryStore
from src.core.schema_graph import SchemaGraph
from src.core.schema_loader import load
from src.guardrails.output_filter import OutputFilter
from src.guardrails.view_scope import ViewScope
from src.knowledge.embeddings import EmbeddingClient
from src.knowledge.indexer import KBIndexer
from src.knowledge.retriever import KBRetriever
from src.llm.base import BaseLLMClient
from src.planner.plan_validator import PlanValidationError
from src.planner.planner import Planner

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class _NoopEmbedder:
    dimension = 4

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), self.dimension), dtype=np.float32)


class _ScriptedLLM(BaseLLMClient):
    """Scripted LLM that returns whatever plan dict was queued."""

    def __init__(self) -> None:
        super().__init__(model="scripted")
        self.next: dict[str, Any] | None = None
        self.last_prompt: str | None = None

    async def tool_call(  # type: ignore[override]
        self,
        prompt: str,
        tool_schema: dict[str, Any],
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        self.last_prompt = prompt
        assert self.next is not None, "no scripted response queued"
        return self.next

    async def text_complete(  # type: ignore[override]
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        raise NotImplementedError


@pytest.fixture
def graph() -> SchemaGraph:
    g = load(REAL_SCHEMA)
    store = InMemoryStore(g, DATA_DIR)
    emb: EmbeddingClient = _NoopEmbedder()  # type: ignore[assignment]
    kb = KBRetriever(store, KBIndexer(emb))
    g.bind_runtime(store=store, kb=kb)
    return g


@pytest.fixture
def planner(graph: SchemaGraph) -> Planner:
    return Planner(_ScriptedLLM(), graph)


@pytest.fixture
def scripted(planner: Planner) -> _ScriptedLLM:
    return planner._llm  # type: ignore[return-value]


def _scope_end_user_john() -> ViewScope:
    return ViewScope(
        role="end_user",
        user_sys_id="usr001",
        actor_name="John Doe",
        visible_user_sys_ids=frozenset({"usr001", "usr009"}),
    )


def _scope_agent_ravi() -> ViewScope:
    return ViewScope(
        role="agent",
        user_sys_id="usr003",
        actor_name="Ravi Kumar",
        group_sys_ids=("grp_desktop", "grp_network"),
        visible_user_sys_ids=frozenset(
            # Ravi himself + members of his groups + his manager.
            {"usr003", "usr008", "usr010"}
        ),
    )


def _scope_manager_deepak() -> ViewScope:
    return ViewScope(
        role="manager",
        user_sys_id="usr010",
        actor_name="Deepak Sharma",
        direct_report_sys_ids=("usr003", "usr005", "usr008"),
        managed_group_sys_ids=(
            "grp_network", "grp_desktop", "grp_facilities", "grp_cloud",
            "grp_security",
        ),
        visible_user_sys_ids=frozenset(
            {"usr010", "usr003", "usr005", "usr008"}
        ),
    )


# ----------------------------------------------------------- end-to-end


@pytest.mark.asyncio
async def test_end_user_find_incidents_returns_only_own(
    planner: Planner, scripted: _ScriptedLLM, graph: SchemaGraph,
) -> None:
    scripted.next = {
        "intent": "lookup",
        "reasoning": "list incidents in scope " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "incs", "entity": "incident", "filters": []},
        ],
        "output_spec": {"format": "list", "final_var": "incs",
                        "max_items_shown": 50},
        "confidence": 0.9,
    }
    plan = await planner.plan("show me my tickets", view_scope=_scope_end_user_john())
    result = await graph.execute_plan(
        plan, view_scope=_scope_end_user_john(),
    )
    records = result.output
    assert isinstance(records, list)
    # Every record's caller_id must be John's sys_id.
    for r in records:
        assert r["caller_id"] == "usr001", r
    assert len(records) >= 1, "John should have at least one incident"


@pytest.mark.asyncio
async def test_agent_find_incidents_covers_queue_and_groups(
    planner: Planner, scripted: _ScriptedLLM, graph: SchemaGraph,
) -> None:
    scripted.next = {
        "intent": "lookup",
        "reasoning": "list incidents in scope " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "incs", "entity": "incident", "filters": []},
        ],
        "output_spec": {"format": "list", "final_var": "incs",
                        "max_items_shown": 50},
        "confidence": 0.9,
    }
    scope = _scope_agent_ravi()
    plan = await planner.plan("what's on my queue", view_scope=scope)
    result = await graph.execute_plan(plan, view_scope=scope)
    records = result.output
    assert isinstance(records, list)
    for r in records:
        in_queue = r.get("assigned_to") == "usr003"
        in_group = r.get("assignment_group") in scope.group_sys_ids
        assert in_queue or in_group, r


@pytest.mark.asyncio
async def test_manager_find_incidents_covers_reports(
    planner: Planner, scripted: _ScriptedLLM, graph: SchemaGraph,
) -> None:
    scripted.next = {
        "intent": "lookup",
        "reasoning": "list incidents in scope " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "incs", "entity": "incident", "filters": []},
        ],
        "output_spec": {"format": "list", "final_var": "incs",
                        "max_items_shown": 50},
        "confidence": 0.9,
    }
    scope = _scope_manager_deepak()
    plan = await planner.plan("my team's tickets", view_scope=scope)
    result = await graph.execute_plan(plan, view_scope=scope)
    records = result.output
    assert isinstance(records, list)
    in_scope_users = {scope.user_sys_id, *scope.direct_report_sys_ids}
    for r in records:
        touched = r.get("caller_id") in in_scope_users or r.get("assigned_to") in in_scope_users
        assert touched, r


@pytest.mark.asyncio
async def test_admin_find_incidents_sees_everything(
    planner: Planner, scripted: _ScriptedLLM, graph: SchemaGraph,
) -> None:
    scripted.next = {
        "intent": "lookup",
        "reasoning": "list all incidents " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "incs", "entity": "incident", "filters": []},
        ],
        "output_spec": {"format": "list", "final_var": "incs",
                        "max_items_shown": 50},
        "confidence": 0.9,
    }
    # Admin scope is the no-op; pass None to confirm the same path.
    plan = await planner.plan("list all incidents", view_scope=None)
    result = await graph.execute_plan(plan, view_scope=None)
    records = result.output
    assert isinstance(records, list)
    assert len(records) >= 5, "admin should see every incident in the fixture"


# --------------------------------- unfiltered find under non-admin scopes


@pytest.mark.asyncio
async def test_manager_unfiltered_find_users_returns_scoped_team(
    planner: Planner, scripted: _ScriptedLLM, graph: SchemaGraph,
) -> None:
    """Unfiltered find on sys_user narrows to self + direct reports."""
    scripted.next = {
        "intent": "lookup",
        "reasoning": "list manager's visible users " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "u", "entity": "sys_user", "filters": []},
        ],
        "output_spec": {"format": "list", "final_var": "u",
                        "max_items_shown": 50},
        "confidence": 0.9,
    }
    scope = _scope_manager_deepak()
    plan = await planner.plan("show me all users in my team", view_scope=scope)
    result = await graph.execute_plan(plan, view_scope=scope)
    records = result.output
    assert isinstance(records, list)
    sys_ids = {r["sys_id"] for r in records}
    # Deepak himself + his three direct reports.
    expected = {"usr010", "usr003", "usr005", "usr008"}
    assert sys_ids == expected, sys_ids


@pytest.mark.asyncio
async def test_agent_unfiltered_find_users_returns_group_mates(
    planner: Planner, scripted: _ScriptedLLM, graph: SchemaGraph,
) -> None:
    scripted.next = {
        "intent": "lookup",
        "reasoning": "list users in scope " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "u", "entity": "sys_user", "filters": []},
        ],
        "output_spec": {"format": "list", "final_var": "u",
                        "max_items_shown": 50},
        "confidence": 0.9,
    }
    scope = _scope_agent_ravi()
    plan = await planner.plan("who's on my team", view_scope=scope)
    result = await graph.execute_plan(plan, view_scope=scope)
    records = result.output
    assert isinstance(records, list)
    sys_ids = {r["sys_id"] for r in records}
    # Ravi + Priya (also in grp_desktop) + Deepak (Ravi's manager).
    assert sys_ids == {"usr003", "usr008", "usr010"}, sys_ids


# ------------------------------------------------------- validator refuses


@pytest.mark.asyncio
async def test_end_user_listing_users_is_rejected_by_validator(
    planner: Planner, scripted: _ScriptedLLM,
) -> None:
    # Planner *should* have set out_of_scope, but assume it slipped:
    # the validator must reject an unfiltered find on sys_user for a
    # non-admin role.
    scripted.next = {
        "intent": "cross_reference",
        "reasoning": "list all users (caller filtered out) " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "all", "entity": "sys_user", "filters": []},
        ],
        "output_spec": {"format": "list", "final_var": "all",
                        "max_items_shown": 50},
        "confidence": 0.9,
    }
    with pytest.raises(PlanValidationError) as exc:
        await planner.plan("list all users", view_scope=_scope_end_user_john())
    msg = "; ".join(exc.value.errors)
    assert "end_user" in msg
    assert "sys_user" in msg or "name" in msg


@pytest.mark.asyncio
async def test_end_user_cannot_aggregate(
    planner: Planner, scripted: _ScriptedLLM,
) -> None:
    scripted.next = {
        "intent": "analytical",
        "reasoning": "count incidents in scope " + ("x" * 20),
        "operations": [
            {"op": "find", "id": "incs", "entity": "incident", "filters": []},
            {"op": "aggregate", "id": "n", "source": "$incs",
             "operation": "count"},
        ],
        "output_spec": {"format": "scalar", "final_var": "n",
                        "max_items_shown": 1},
        "confidence": 0.9,
    }
    with pytest.raises(PlanValidationError) as exc:
        await planner.plan("how many tickets", view_scope=_scope_end_user_john())
    msg = "; ".join(exc.value.errors)
    assert "aggregate" in msg.lower()


# ---------------------------------------------------------- pii stripping


def test_pii_stripped_for_non_admin(graph: SchemaGraph) -> None:
    # OutputFilter from a non-admin ViewScope must redact `sys_user.email`.
    scope = _scope_end_user_john()
    filt = OutputFilter.from_view_scope(graph, scope)
    record = {
        "sys_id": "usr001",
        "name": "John Doe",
        "email": "john.doe@acme.com",
        "department": "Engineering",
    }
    out = filt.filter_record(record, "sys_user")
    assert out["email"] == "[REDACTED]"
    assert out["name"] == "John Doe"


def test_pii_visible_for_admin(graph: SchemaGraph) -> None:
    filt = OutputFilter.from_view_scope(graph, ViewScope.admin())
    record = {
        "sys_id": "usr001",
        "name": "John Doe",
        "email": "john.doe@acme.com",
    }
    out = filt.filter_record(record, "sys_user")
    assert out.get("email") == "john.doe@acme.com"
