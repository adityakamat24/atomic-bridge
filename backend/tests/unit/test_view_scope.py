"""Unit tests for the ViewScope matrix (row filter + validator helpers)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.in_memory_store import InMemoryStore
from src.core.schema_loader import load
from src.guardrails.view_scope import ViewScope, derive_role

REAL_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# ---------------------------------------------------------------------- ctor


def test_admin_is_no_op() -> None:
    s = ViewScope.admin()
    assert s.is_admin is True
    assert s.can_view_pii is True
    assert s.can_aggregate() is True

    records = [{"sys_id": "x"}, {"sys_id": "y"}]
    # Admin passes any entity through, including unknown ones.
    assert s.filter_records("incident", records) == records
    assert s.filter_records("sys_user_group", records) == records
    assert s.filter_records("_aggregate", records) == records


# ---------------------------------------------------------------- end_user


def _end_user_scope() -> ViewScope:
    return ViewScope(
        role="end_user",
        user_sys_id="usr001",
        visible_user_sys_ids=frozenset({"usr001"}),
    )


def test_end_user_filter_incidents_keeps_own_only() -> None:
    s = _end_user_scope()
    records = [
        {"sys_id": "i1", "caller_id": "usr001", "assigned_to": "usr003"},
        {"sys_id": "i2", "caller_id": "usr002", "assigned_to": "usr001"},
        {"sys_id": "i3", "caller_id": "usr001", "assigned_to": None},
    ]
    out = s.filter_records("incident", records)
    assert [r["sys_id"] for r in out] == ["i1", "i3"]


def test_end_user_filter_users_keeps_self_only() -> None:
    s = _end_user_scope()
    records = [
        {"sys_id": "usr001"}, {"sys_id": "usr002"}, {"sys_id": "usr003"},
    ]
    out = s.filter_records("sys_user", records)
    assert [r["sys_id"] for r in out] == ["usr001"]


def test_end_user_filter_groups_drops_all() -> None:
    s = _end_user_scope()
    records = [{"sys_id": "grp_desktop"}, {"sys_id": "grp_network"}]
    assert s.filter_records("sys_user_group", records) == []


def test_end_user_kb_and_category_are_public() -> None:
    s = _end_user_scope()
    kbs = [{"sys_id": "kb1"}, {"sys_id": "kb2"}]
    cats = [{"sys_id": "Network"}]
    assert s.filter_records("kb_knowledge", kbs) == kbs
    assert s.filter_records("category", cats) == cats


def test_end_user_cannot_aggregate() -> None:
    s = _end_user_scope()
    assert s.can_aggregate() is False
    assert s.reject_aggregate() is not None


def test_end_user_rejects_unfiltered_user_find() -> None:
    s = _end_user_scope()
    assert s.reject_find("sys_user", has_pk_or_name_filter=False) is not None
    # A name filter unlocks the find; the row filter narrows the result.
    assert s.reject_find("sys_user", has_pk_or_name_filter=True) is None


def test_end_user_rejects_group_lookup_entirely() -> None:
    s = _end_user_scope()
    assert s.reject_find("sys_user_group", has_pk_or_name_filter=False) is not None


def test_end_user_loses_pii_visibility() -> None:
    s = _end_user_scope()
    assert s.can_view_pii is False


# ------------------------------------------------------------------- agent


def _agent_scope() -> ViewScope:
    return ViewScope(
        role="agent",
        user_sys_id="usr003",
        group_sys_ids=("grp_desktop", "grp_network"),
        visible_user_sys_ids=frozenset({"usr003", "usr008", "usr010"}),
    )


def test_agent_filter_incidents_assigned_or_in_group() -> None:
    s = _agent_scope()
    records = [
        # Assigned to agent → visible.
        {"sys_id": "i1", "caller_id": "usr001", "assigned_to": "usr003"},
        # Handled by one of agent's groups → visible.
        {"sys_id": "i2", "caller_id": "usr002", "assigned_to": "usr008",
         "assignment_group": "grp_desktop"},
        # Outside agent's groups, not assigned to agent → hidden.
        {"sys_id": "i3", "caller_id": "usr004", "assigned_to": "usr005",
         "assignment_group": "grp_facilities"},
    ]
    out = s.filter_records("incident", records)
    assert sorted(r["sys_id"] for r in out) == ["i1", "i2"]


def test_agent_filter_users_uses_visible_set() -> None:
    s = _agent_scope()
    records = [
        {"sys_id": "usr001"},
        {"sys_id": "usr003"},  # self
        {"sys_id": "usr008"},  # team-mate
        {"sys_id": "usr010"},  # own manager
        {"sys_id": "usr099"},
    ]
    out = s.filter_records("sys_user", records)
    assert sorted(r["sys_id"] for r in out) == ["usr003", "usr008", "usr010"]


def test_agent_filter_groups_keeps_own_only() -> None:
    s = _agent_scope()
    records = [
        {"sys_id": "grp_desktop"},
        {"sys_id": "grp_network"},
        {"sys_id": "grp_facilities"},
    ]
    out = s.filter_records("sys_user_group", records)
    assert sorted(r["sys_id"] for r in out) == ["grp_desktop", "grp_network"]


def test_agent_can_aggregate() -> None:
    s = _agent_scope()
    assert s.can_aggregate() is True
    assert s.reject_aggregate() is None


def test_agent_unfiltered_user_find_is_allowed() -> None:
    # Agents have a non-trivial visible set; the row filter narrows it.
    s = _agent_scope()
    assert s.reject_find("sys_user", has_pk_or_name_filter=False) is None
    assert s.reject_find("sys_user", has_pk_or_name_filter=True) is None


def test_manager_unfiltered_user_find_is_allowed() -> None:
    s = _manager_scope()
    assert s.reject_find("sys_user", has_pk_or_name_filter=False) is None
    assert s.reject_find("sys_user_group", has_pk_or_name_filter=False) is None


# ---------------------------------------------------------------- manager


def _manager_scope() -> ViewScope:
    return ViewScope(
        role="manager",
        user_sys_id="usr010",
        direct_report_sys_ids=("usr003", "usr005", "usr008"),
        managed_group_sys_ids=("grp_network", "grp_desktop"),
        visible_user_sys_ids=frozenset(
            {"usr010", "usr003", "usr005", "usr008"}
        ),
    )


def test_manager_filter_incidents_covers_reports() -> None:
    s = _manager_scope()
    records = [
        # Direct report is caller → visible.
        {"sys_id": "i1", "caller_id": "usr003", "assigned_to": "usr099"},
        # Direct report is assignee → visible.
        {"sys_id": "i2", "caller_id": "usr002", "assigned_to": "usr008"},
        # Manager themselves are caller → visible (self counts).
        {"sys_id": "i3", "caller_id": "usr010", "assigned_to": "usr099"},
        # No report involvement → hidden.
        {"sys_id": "i4", "caller_id": "usr001", "assigned_to": "usr099"},
    ]
    out = s.filter_records("incident", records)
    assert sorted(r["sys_id"] for r in out) == ["i1", "i2", "i3"]


def test_manager_filter_groups_keeps_managed_only() -> None:
    s = _manager_scope()
    records = [
        {"sys_id": "grp_network"},
        {"sys_id": "grp_desktop"},
        {"sys_id": "grp_facilities"},
    ]
    out = s.filter_records("sys_user_group", records)
    assert sorted(r["sys_id"] for r in out) == ["grp_desktop", "grp_network"]


def test_manager_filter_users_includes_self_and_reports() -> None:
    s = _manager_scope()
    records = [
        {"sys_id": "usr001"},
        {"sys_id": "usr003"},
        {"sys_id": "usr010"},
        {"sys_id": "usr099"},
    ]
    out = s.filter_records("sys_user", records)
    assert sorted(r["sys_id"] for r in out) == ["usr003", "usr010"]


def test_manager_can_aggregate() -> None:
    s = _manager_scope()
    assert s.can_aggregate() is True


# --------------------------------------------------- planner-prompt context


@pytest.mark.parametrize("role", ["end_user", "agent", "manager"])
def test_planner_prompt_block_includes_role_and_actor(role: str) -> None:
    s = ViewScope(
        role=role,  # type: ignore[arg-type]
        user_sys_id="usr003",
        actor_name="Ravi Kumar",
        group_sys_ids=("grp_desktop",),
        direct_report_sys_ids=("usr005",),
        managed_group_sys_ids=("grp_desktop",),
        visible_user_sys_ids=frozenset({"usr003", "usr005"}),
    )
    block = s.context_for_planner_prompt()
    assert "Ravi Kumar" in block
    assert role in block
    assert "Out of scope" in block


def test_admin_planner_prompt_is_full_access() -> None:
    block = ViewScope.admin().context_for_planner_prompt()
    assert "admin" in block.lower()
    assert "full access" in block.lower()


# ----------------------------------------------------------- derive_role


@pytest.fixture
def store() -> InMemoryStore:
    g = load(REAL_SCHEMA)
    return InMemoryStore(g, DATA_DIR)


def test_derive_role_john_doe_is_end_user(store: InMemoryStore) -> None:
    user = store.get("sys_user", "usr001")
    assert user is not None
    assert derive_role(user, store) == "end_user"


def test_derive_role_ravi_is_agent(store: InMemoryStore) -> None:
    user = store.get("sys_user", "usr003")
    assert user is not None
    assert derive_role(user, store) == "agent"


def test_derive_role_deepak_is_manager(store: InMemoryStore) -> None:
    # Deepak has both reports and managed groups; manager wins.
    user = store.get("sys_user", "usr010")
    assert user is not None
    assert derive_role(user, store) == "manager"


def test_derive_role_alex_director_is_manager(store: InMemoryStore) -> None:
    # Reports only (no member_groups) still derives manager.
    user = store.get("sys_user", "usr009")
    assert user is not None
    assert derive_role(user, store) == "manager"


def test_derive_role_falls_back_to_end_user_for_empty_data() -> None:
    class _EmptyStore:
        def find(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return []

        def get(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return None

    assert (
        derive_role({"sys_id": "ghost", "member_groups": []}, _EmptyStore())  # type: ignore[arg-type]
        == "end_user"
    )
