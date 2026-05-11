from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.data_store import Filter, Record
from src.core.schema_graph import SchemaGraph


class InMemoryStore:
    """JSON-backed implementation of the `DataStore` protocol.

    On startup it reads one JSON file per entity (using the entity's
    `data_path` from the schema). Mutations are persisted back on every
    write. Persistence is best-effort and intentionally simple — the design
    doc names the production swap (Postgres / real ServiceNow API).

    All `get`/`find`/`create`/`update` results are deep-copied so callers
    cannot mutate stored state by accident.
    """

    def __init__(self, graph: SchemaGraph, data_dir: Path | str) -> None:
        self._graph = graph
        self._data_dir = Path(data_dir)
        # entity_id -> sys_id -> record
        self._data: dict[str, dict[str, Record]] = {}
        self._load_all()

    # ------------------------------------------------------------------ load

    def _load_all(self) -> None:
        for entity in self._graph.all_entities():
            path = self._data_dir / entity.data_path
            if not path.exists():
                self._data[entity.id] = {}
                continue
            records = json.loads(path.read_text(encoding="utf-8"))
            pk = entity.primary_key
            self._data[entity.id] = {r[pk]: r for r in records}

    def _persist(self, entity: str) -> None:
        ent = self._graph.entity(entity)
        path = self._data_dir / ent.data_path
        records = list(self._data[entity].values())
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    # ----------------------------------------------------------------- reads

    def get(self, entity: str, sys_id: str) -> Record | None:
        rec = self._data.get(entity, {}).get(sys_id)
        return deepcopy(rec) if rec is not None else None

    def get_many(self, entity: str, sys_ids: list[str]) -> list[Record]:
        bucket = self._data.get(entity, {})
        return [deepcopy(bucket[sid]) for sid in sys_ids if sid in bucket]

    def find(
        self, entity: str, filters: list[Filter], limit: int = 100
    ) -> list[Record]:
        bucket = self._data.get(entity, {})
        records = list(bucket.values())
        records = _apply_filters(records, filters, entity, self._graph)
        return [deepcopy(r) for r in records[:limit]]

    def count(self, entity: str, filters: list[Filter]) -> int:
        bucket = self._data.get(entity, {})
        records = list(bucket.values())
        return len(_apply_filters(records, filters, entity, self._graph))

    # ---------------------------------------------------------------- writes

    def create(self, entity: str, fields: dict[str, Any]) -> Record:
        ent = self._graph.entity(entity)
        pk = ent.primary_key
        new_id = fields.get(pk) or self._gen_sys_id(entity)
        now = _utc_now_iso()
        record: Record = {
            pk: new_id,
            "sys_created_on": now,
            "sys_updated_on": now,
            **fields,
        }
        record[pk] = new_id  # ensure pk overrides anything in fields
        if entity == "incident" and "number" not in record:
            record["number"] = self._gen_incident_number()
        self._data.setdefault(entity, {})[new_id] = record
        self._persist(entity)
        return deepcopy(record)

    def update(self, entity: str, sys_id: str, fields: dict[str, Any]) -> Record:
        bucket = self._data.get(entity, {})
        if sys_id not in bucket:
            raise KeyError(f"{entity}/{sys_id} not found")
        record = bucket[sys_id]
        record.update(fields)
        record["sys_updated_on"] = _utc_now_iso()
        self._persist(entity)
        return deepcopy(record)

    # -------------------------------------------------------------- helpers

    def _gen_sys_id(self, entity: str) -> str:
        prefix = entity[:3] if len(entity) >= 3 else entity
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _gen_incident_number(self) -> str:
        max_n = 0
        for r in self._data.get("incident", {}).values():
            num = str(r.get("number", ""))
            if num.startswith("INC"):
                try:
                    max_n = max(max_n, int(num[3:]))
                except ValueError:
                    continue
        return f"INC{max_n + 1:07d}"


# =============================================================================
# Filter evaluation. Module-level so the executor can reuse it for post-
# traversal filtering (see `04-executor.md` apply_filters reference).
# =============================================================================


def apply_filters(
    records: list[Record],
    filters: list[Filter],
    entity_id: str,
    graph: SchemaGraph,
) -> list[Record]:
    """Public wrapper. Returns records that match ALL filters (AND semantics)."""
    return _apply_filters(records, filters, entity_id, graph)


def _apply_filters(
    records: list[Record],
    filters: list[Filter],
    entity_id: str,
    graph: SchemaGraph,
) -> list[Record]:
    out = list(records)
    for f in filters:
        out = [r for r in out if _matches(r, f, entity_id, graph)]
    return out


def _matches(record: Record, f: Filter, entity_id: str, graph: SchemaGraph) -> bool:
    field_name = f.field.split(".")[-1]
    full_field_id = f.field if "." in f.field else f"{entity_id}.{f.field}"
    rv = record.get(field_name)

    op = f.operator
    if op == "is_null":
        return rv is None
    if op == "is_not_null":
        return rv is not None

    # All remaining operators need a non-null record value.
    if rv is None:
        return False

    value = _translate_value(f.value, full_field_id, graph)

    if op == "in":
        if not isinstance(value, list):
            return False
        return rv in value

    rv_cmp: Any = rv
    value_cmp: Any = value
    if (
        isinstance(rv, str)
        and isinstance(value, str)
        and not f.case_sensitive
    ):
        rv_cmp = rv.lower()
        value_cmp = value.lower()

    if op == "eq":
        return bool(rv_cmp == value_cmp)
    if op == "neq":
        return bool(rv_cmp != value_cmp)
    if op == "contains":
        if isinstance(rv_cmp, str) and isinstance(value_cmp, str):
            return value_cmp in rv_cmp
        if isinstance(rv, list):
            return value in rv
        return False
    if op == "gt":
        return bool(rv > value)
    if op == "lt":
        return bool(rv < value)
    if op == "gte":
        return bool(rv >= value)
    if op == "lte":
        return bool(rv <= value)
    return False


def _translate_value(
    value: Any, field_id: str, graph: SchemaGraph
) -> Any:
    """Translate display strings to integer codes for value-mapped fields.

    Pass-through for unmapped fields, integer codes, None, and unknown labels.
    The validator (Phase 5) is the strict gate; the store is permissive so a
    direct API caller passing 'In Progress' still gets results.
    """
    if value is None:
        return value
    if not graph.has_field(field_id):
        return value
    field = graph.field(field_id)
    if field.value_map_id is None:
        return value
    vm = graph.value_map(field.value_map_id)

    def translate_one(v: Any) -> Any:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            fuzzy = vm.fuzzy_from_display(v)
            if fuzzy is not None:
                return fuzzy
            try:
                return vm.from_display(v)
            except KeyError:
                return v
        return v

    if isinstance(value, list):
        return [translate_one(v) for v in value]
    return translate_one(value)


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
