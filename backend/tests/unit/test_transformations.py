from __future__ import annotations

from pathlib import Path

import pytest

from src.core.schema_loader import load
from src.core.transformations import apply_value_maps, display_to_code

REPO_DATA_SCHEMA = Path(__file__).resolve().parents[2] / "data" / "schema.yaml"


def test_apply_value_maps_translates_codes_to_display_strings() -> None:
    g = load(REPO_DATA_SCHEMA)
    record = {
        "sys_id": "inc-x",
        "number": "INC0099999",
        "state": 2,
        "priority": 1,
        "category": "Network",
    }
    out = apply_value_maps(record, "incident", g)
    assert out["state"] == "In Progress"
    assert out["priority"] == "Critical"
    assert out["category"] == "Network"  # untouched
    # Original record is unchanged.
    assert record["state"] == 2


def test_apply_value_maps_passes_through_unknown_codes() -> None:
    g = load(REPO_DATA_SCHEMA)
    record = {"state": 99}
    out = apply_value_maps(record, "incident", g)
    assert out["state"] == 99


def test_apply_value_maps_ignores_fields_without_value_map() -> None:
    g = load(REPO_DATA_SCHEMA)
    record = {"name": "John Doe", "department": "Engineering"}
    out = apply_value_maps(record, "sys_user", g)
    assert out == {"name": "John Doe", "department": "Engineering"}


def test_display_to_code_uses_fuzzy_match() -> None:
    g = load(REPO_DATA_SCHEMA)
    assert display_to_code("In Progress", "incident.state", g) == 2
    assert display_to_code("in progress", "incident.state", g) == 2
    assert display_to_code("In-Progress", "incident.state", g) == 2


def test_display_to_code_passes_int_through() -> None:
    g = load(REPO_DATA_SCHEMA)
    assert display_to_code(3, "incident.state", g) == 3


def test_display_to_code_unknown_label_raises() -> None:
    g = load(REPO_DATA_SCHEMA)
    with pytest.raises(KeyError):
        display_to_code("Bogus", "incident.state", g)


def test_display_to_code_passes_through_when_no_value_map() -> None:
    g = load(REPO_DATA_SCHEMA)
    assert display_to_code("Network", "incident.category", g) == "Network"
