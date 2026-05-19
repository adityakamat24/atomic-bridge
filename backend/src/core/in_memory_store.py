from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.data_store import Filter, Record
from src.core.filters import apply_filters as _apply_filters
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


# Public re-export. Implementation lives in core/filters.py so the
# SchemaGraph's execution layer can use the same logic without a circular
# import. Existing callers that import `apply_filters` from this module
# continue to work.
apply_filters = _apply_filters


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
