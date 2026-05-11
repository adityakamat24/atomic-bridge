from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.core.data_store import Filter
from src.core.in_memory_store import InMemoryStore
from src.core.schema_graph import SchemaGraph
from src.core.schema_loader import load

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def graph() -> SchemaGraph:
    return load(REPO_DATA_DIR / "schema.yaml")


@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    """Returns a writable copy of backend/data/ so mutation tests cannot
    pollute the real fixtures.
    """
    target = tmp_path / "data"
    shutil.copytree(REPO_DATA_DIR, target)
    return target


@pytest.fixture
def store(graph: SchemaGraph, isolated_data_dir: Path) -> InMemoryStore:
    return InMemoryStore(graph, isolated_data_dir)


# ---------- Loading ---------------------------------------------------------


def test_loads_all_entity_buckets(store: InMemoryStore) -> None:
    # 5 incidents from PDF + 1 synthetic (INC0012350) for dangling-ref test
    assert len(store._data["incident"]) == 6  # noqa: SLF001
    assert len(store._data["sys_user"]) == 10  # noqa: SLF001
    assert len(store._data["sys_user_group"]) == 5  # noqa: SLF001
    assert len(store._data["kb_knowledge"]) == 5  # noqa: SLF001


def test_missing_data_file_yields_empty_bucket(
    graph: SchemaGraph, tmp_path: Path
) -> None:
    # Build a minimal data dir with only one of the four files.
    (tmp_path / "users.json").write_text("[]", encoding="utf-8")
    s = InMemoryStore(graph, tmp_path)
    assert s._data["sys_user"] == {}  # noqa: SLF001
    assert s._data["incident"] == {}  # noqa: SLF001


# ---------- get / get_many --------------------------------------------------


def test_get_returns_record_for_known_sys_id(store: InMemoryStore) -> None:
    user = store.get("sys_user", "usr001")
    assert user is not None
    assert user["name"] == "John Doe"
    assert user["department"] == "Engineering"


def test_get_returns_none_for_unknown_sys_id(store: InMemoryStore) -> None:
    assert store.get("sys_user", "usr999") is None
    assert store.get("incident", "missing") is None


def test_get_returns_deep_copy(store: InMemoryStore) -> None:
    user = store.get("sys_user", "usr001")
    assert user is not None
    user["name"] = "MUTATED"
    again = store.get("sys_user", "usr001")
    assert again is not None
    assert again["name"] == "John Doe"


def test_get_many_batches_with_missing_silently_dropped(store: InMemoryStore) -> None:
    users = store.get_many("sys_user", ["usr001", "usr999", "usr002"])
    assert [u["sys_id"] for u in users] == ["usr001", "usr002"]


# ---------- find: each operator --------------------------------------------


def test_find_eq_with_integer_value(store: InMemoryStore) -> None:
    in_progress = store.find(
        "incident", [Filter(field="state", operator="eq", value=2)]
    )
    numbers = {r["number"] for r in in_progress}
    assert numbers == {"INC0012345", "INC0012348", "INC0012349"}


def test_find_eq_with_value_map_display_string(store: InMemoryStore) -> None:
    """Display strings are translated to codes by the store."""
    in_progress = store.find(
        "incident", [Filter(field="state", operator="eq", value="In Progress")]
    )
    assert {r["number"] for r in in_progress} == {
        "INC0012345",
        "INC0012348",
        "INC0012349",
    }


def test_find_eq_is_case_insensitive_for_strings(store: InMemoryStore) -> None:
    eng = store.find(
        "sys_user", [Filter(field="department", operator="eq", value="ENGINEERING")]
    )
    assert {u["name"] for u in eng} == {"John Doe", "Tom Brown", "Alex Morgan"}


def test_find_eq_case_sensitive_when_requested(store: InMemoryStore) -> None:
    eng = store.find(
        "sys_user",
        [Filter(field="department", operator="eq", value="engineering", case_sensitive=True)],
    )
    assert eng == []


def test_find_neq_returns_complement(store: InMemoryStore) -> None:
    not_resolved = store.find(
        "incident", [Filter(field="state", operator="neq", value=4)]
    )
    # PDF data has no Resolved incidents, so this just sanity-checks the
    # neq operator: every row must have state != 4.
    assert all(r["state"] != 4 for r in not_resolved)


def test_find_in_with_value_map_displays(store: InMemoryStore) -> None:
    open_states = store.find(
        "incident",
        [Filter(field="state", operator="in", value=["New", "In Progress", "On Hold"])],
    )
    assert all(r["state"] in (1, 2, 3) for r in open_states)
    # All 6 incidents in the fixture are open (5 PDF + INC0012350 dangling).
    assert len(open_states) == 6


def test_find_in_with_integer_list(store: InMemoryStore) -> None:
    open_states = store.find(
        "incident",
        [Filter(field="state", operator="in", value=[1, 2, 3])],
    )
    assert len(open_states) == 6


def test_find_contains_substring_case_insensitive(store: InMemoryStore) -> None:
    vpn = store.find(
        "incident",
        [Filter(field="short_description", operator="contains", value="vpn")],
    )
    assert {r["number"] for r in vpn} == {"INC0012345"}


def test_find_gt_lt_gte_lte_on_priority_codes(store: InMemoryStore) -> None:
    """Lower priority code = more severe. priority<3 means Critical or High."""
    severe = store.find(
        "incident", [Filter(field="priority", operator="lt", value=3)]
    )
    assert all(r["priority"] in (1, 2) for r in severe)

    not_severe = store.find(
        "incident", [Filter(field="priority", operator="gte", value=3)]
    )
    assert all(r["priority"] >= 3 for r in not_severe)


def test_find_is_null_finds_unassigned_incident(store: InMemoryStore) -> None:
    unassigned = store.find(
        "incident", [Filter(field="assigned_to", operator="is_null")]
    )
    assert {r["number"] for r in unassigned} == {"INC0012346"}


def test_find_is_not_null_excludes_unassigned(store: InMemoryStore) -> None:
    assigned = store.find(
        "incident", [Filter(field="assigned_to", operator="is_not_null")]
    )
    assert "INC0012346" not in {r["number"] for r in assigned}


def test_find_combined_filters_and_semantics(store: InMemoryStore) -> None:
    high_open = store.find(
        "incident",
        [
            Filter(field="state", operator="in", value=["New", "In Progress", "On Hold"]),
            Filter(field="priority", operator="eq", value="High"),
        ],
    )
    # In PDF data, High priority (=2) open incidents are INC0012345 + INC0012348.
    # INC0012350 (synthetic) is also priority 2 + state 1 (New).
    assert {r["number"] for r in high_open} == {
        "INC0012345",
        "INC0012348",
        "INC0012350",
    }


def test_find_respects_limit(store: InMemoryStore) -> None:
    res = store.find("incident", [], limit=3)
    assert len(res) == 3


def test_find_qualified_field_name_works(store: InMemoryStore) -> None:
    """Filters can be either 'state' or 'incident.state'."""
    a = store.find("incident", [Filter(field="state", operator="eq", value=2)])
    b = store.find("incident", [Filter(field="incident.state", operator="eq", value=2)])
    assert {r["number"] for r in a} == {r["number"] for r in b}


def test_find_returns_deep_copies(store: InMemoryStore) -> None:
    [first] = store.find(
        "incident",
        [Filter(field="number", operator="eq", value="INC0012345")],
    )
    first["short_description"] = "MUTATED"
    [again] = store.find(
        "incident",
        [Filter(field="number", operator="eq", value="INC0012345")],
    )
    assert again["short_description"] != "MUTATED"


# ---------- count -----------------------------------------------------------


def test_count_matches_find_length(store: InMemoryStore) -> None:
    flt = [Filter(field="department", operator="eq", value="Engineering")]
    assert store.count("sys_user", flt) == 3


def test_count_critical_priority_is_one(store: InMemoryStore) -> None:
    """Sanity-checks the eval expectation that critical count = 1."""
    n = store.count(
        "incident", [Filter(field="priority", operator="eq", value="Critical")]
    )
    assert n == 1


# ---------- create ----------------------------------------------------------


def test_create_generates_sys_id_and_persists(
    store: InMemoryStore, isolated_data_dir: Path
) -> None:
    new = store.create(
        "incident",
        {
            "short_description": "Printer offline in conference room",
            "state": 1,
            "priority": 3,
            "category": "Hardware",
            "caller_id": "usr001",
            "assignment_group": "grp001",
        },
    )
    assert new["sys_id"].startswith("inc-")
    assert new["number"].startswith("INC")
    assert new["sys_created_on"] == new["sys_updated_on"]

    # Persisted to disk
    on_disk = json.loads((isolated_data_dir / "incidents.json").read_text(encoding="utf-8"))
    assert any(r["sys_id"] == new["sys_id"] for r in on_disk)


def test_create_increments_incident_number(store: InMemoryStore) -> None:
    new = store.create(
        "incident",
        {
            "short_description": "Test",
            "state": 1,
            "priority": 3,
            "category": "Software",
            "caller_id": "usr001",
            "assignment_group": "grp_desktop",
        },
    )
    # Existing max in fixture is INC0012350 -> next is INC0012351
    assert new["number"] == "INC0012351"


def test_create_preserves_explicit_caller_id(store: InMemoryStore) -> None:
    new = store.create(
        "incident",
        {
            "short_description": "Test",
            "state": 1,
            "priority": 3,
            "category": "Software",
            "caller_id": "usr007",
            "assignment_group": "grp_cloud",
        },
    )
    assert new["caller_id"] == "usr007"


# ---------- update ----------------------------------------------------------


def test_update_mutates_record_and_bumps_sys_updated_on(
    store: InMemoryStore,
) -> None:
    [orig] = store.find(
        "incident", [Filter(field="number", operator="eq", value="INC0012345")]
    )
    original_updated = orig["sys_updated_on"]

    updated = store.update("incident", orig["sys_id"], {"state": 5})
    assert updated["state"] == 5
    assert updated["sys_updated_on"] != original_updated


def test_update_persists_to_disk(
    store: InMemoryStore, isolated_data_dir: Path
) -> None:
    [orig] = store.find(
        "incident", [Filter(field="number", operator="eq", value="INC0012345")]
    )
    store.update("incident", orig["sys_id"], {"state": 5})
    on_disk = json.loads((isolated_data_dir / "incidents.json").read_text(encoding="utf-8"))
    after = next(r for r in on_disk if r["sys_id"] == orig["sys_id"])
    assert after["state"] == 5


def test_update_raises_on_unknown_sys_id(store: InMemoryStore) -> None:
    with pytest.raises(KeyError):
        store.update("incident", "does-not-exist", {"state": 5})


def test_update_does_not_overwrite_other_fields(store: InMemoryStore) -> None:
    [orig] = store.find(
        "incident", [Filter(field="number", operator="eq", value="INC0012345")]
    )
    after = store.update("incident", orig["sys_id"], {"state": 3})
    assert after["short_description"] == orig["short_description"]
    assert after["caller_id"] == orig["caller_id"]


# ---------- reference fields stay as sys_ids on read ------------------------


def test_reference_fields_returned_raw_as_sys_ids(store: InMemoryStore) -> None:
    [vpn] = store.find(
        "incident", [Filter(field="number", operator="eq", value="INC0012345")]
    )
    assert vpn["caller_id"] == "usr001"
    assert vpn["assigned_to"] == "usr003"
    assert vpn["assignment_group"] == "grp_network"


# ---------- value-mapped fields stored as integer codes ---------------------


def test_value_mapped_fields_stored_as_codes_on_read(store: InMemoryStore) -> None:
    [vpn] = store.find(
        "incident", [Filter(field="number", operator="eq", value="INC0012345")]
    )
    assert vpn["state"] == 2  # not "In Progress"
    assert vpn["priority"] == 2  # not "High"
