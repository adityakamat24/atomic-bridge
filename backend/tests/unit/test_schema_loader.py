from __future__ import annotations

from pathlib import Path

import pytest

from src.core.schema_loader import load, loads

REPO_DATA_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "llm_context.md"


# ---------- Loading the real schema.yaml -------------------------------------


def test_real_schema_loads_with_expected_node_counts() -> None:
    g = load(REPO_DATA_SCHEMA)
    counts = g.counts()
    assert counts["entities"] == 4
    assert counts["value_maps"] == 2
    # NOTE: 13-tasks.md Phase 1 says "8 relations" but the YAML in
    # 02-schema-graph.md defines 9. We follow the YAML verbatim.
    assert counts["relations"] == 9


def test_real_schema_field_counts_per_entity() -> None:
    g = load(REPO_DATA_SCHEMA)
    counts = {e.id: len(g.fields_of(e.id)) for e in g.all_entities()}
    assert counts == {
        "incident": 11,
        "sys_user": 6,
        "sys_user_group": 3,
        "kb_knowledge": 5,
    }


def test_real_schema_value_maps_known_codes() -> None:
    g = load(REPO_DATA_SCHEMA)
    state = g.value_map("incident_state")
    assert state.to_display(2) == "In Progress"
    assert state.from_display("Closed") == 5
    priority = g.value_map("incident_priority")
    assert priority.to_display(1) == "Critical"
    assert priority.from_display("Low") == 4


def test_real_schema_relation_metadata_matches_spec() -> None:
    g = load(REPO_DATA_SCHEMA)
    reported = g.relation("incident.reportedBy")
    assert reported.from_entity == "incident"
    assert reported.to_entity == "sys_user"
    assert reported.via_field == "incident.caller_id"
    assert reported.cardinality == "many_to_one"
    assert reported.inverse_relation_id == "sys_user.incidentsReported"


# ---------- shortest_relation_path against the real schema -------------------


def test_real_schema_shortest_path_incident_to_group_uses_handled_by() -> None:
    g = load(REPO_DATA_SCHEMA)
    path = g.shortest_relation_path("incident", "sys_user_group")
    assert path is not None
    assert [r.id for r in path] == ["incident.handledBy"]


def test_real_schema_shortest_path_incident_to_user_uses_reported_or_assigned() -> None:
    g = load(REPO_DATA_SCHEMA)
    path = g.shortest_relation_path("incident", "sys_user")
    assert path is not None
    assert len(path) == 1
    assert path[0].id in {"incident.reportedBy", "incident.assignedTo"}


def test_real_schema_shortest_path_kb_to_anything_returns_none() -> None:
    """kb_knowledge has no outbound relations in the spec schema."""
    g = load(REPO_DATA_SCHEMA)
    assert g.shortest_relation_path("kb_knowledge", "sys_user") is None


# ---------- to_llm_context snapshot ------------------------------------------


def test_real_schema_to_llm_context_matches_snapshot() -> None:
    g = load(REPO_DATA_SCHEMA)
    actual = g.to_llm_context()
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    if actual != expected:
        # Fail loudly with a usable diff hint.
        pytest.fail(
            "to_llm_context output diverged from snapshot. "
            f"To re-baseline: write the new output to {SNAPSHOT_PATH}.\n"
            f"--- expected ---\n{expected[:500]}\n--- actual ---\n{actual[:500]}"
        )


def test_real_schema_to_llm_context_subgraph_filter() -> None:
    g = load(REPO_DATA_SCHEMA)
    sub = g.to_llm_context(entity_ids=["incident"])
    assert "## incident" in sub
    assert "## sys_user " not in sub
    assert "## sys_user_group" not in sub


# ---------- Dynamic entity addition (the key architectural claim) ------------


def test_adding_entity_to_yaml_is_reflected_without_code_changes(tmp_path: Path) -> None:
    """Parse the real schema, append a new entity + relation, dump, reload, and
    verify that `to_llm_context` surfaces the new entity. This proves the
    'add a new ITSM table is a YAML edit' claim from 02-schema-graph.md.
    """
    import yaml

    raw = yaml.safe_load(REPO_DATA_SCHEMA.read_text(encoding="utf-8"))
    raw["entities"].append(
        {
            "id": "change_request",
            "display_name": "Change Request",
            "description": "A planned change to a production service",
            "primary_key": "sys_id",
            "table_name": "change_request",
            "data_path": "change_requests.json",
            "fields": [
                {"name": "number", "display_name": "Change Number", "data_type": "string"},
                {
                    "name": "requested_by",
                    "display_name": "Requested By (sys_id)",
                    "data_type": "reference",
                    "references": "sys_user",
                },
            ],
        }
    )
    raw["relations"].append(
        {
            "id": "change_request.requestedBy",
            "verb_phrase": "requested by",
            "from_entity": "change_request",
            "to_entity": "sys_user",
            "via_field": "change_request.requested_by",
            "cardinality": "many_to_one",
            "description": "Identifies the user who requested the change",
        }
    )

    out_path = tmp_path / "schema.yaml"
    out_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    g = load(out_path)
    assert g.has_entity("change_request")
    assert g.has_relation("change_request.requestedBy")
    ctx = g.to_llm_context()
    assert "change_request" in ctx
    assert "requestedBy" in ctx


# ---------- loads() (string variant) -----------------------------------------


def test_loads_accepts_inline_yaml() -> None:
    yaml_text = """
entities:
  - id: foo
    display_name: "Foo"
    table_name: foo
    data_path: foo.json
    fields:
      - name: bar
        display_name: "Bar"
        data_type: string
"""
    g = loads(yaml_text)
    assert g.has_entity("foo")
    assert g.field("foo.bar").data_type == "string"


def test_loads_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load("/nonexistent/path/schema.yaml")


# ---------- Integrity --------------------------------------------------------


def test_real_schema_integrity_only_flags_known_dangling_inverse() -> None:
    g = load(REPO_DATA_SCHEMA)
    issues = g.integrity_issues()
    # The spec YAML defines `sys_user_group.managedBy` with
    # inverse_relation_id `sys_user.managesGroups`, but never declares that
    # inverse. We treat this as a known spec gap, surfaced (not raised).
    assert issues == [
        "relation sys_user_group.managedBy: inverse_relation_id "
        "sys_user.managesGroups missing"
    ]
