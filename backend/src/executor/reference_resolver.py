from __future__ import annotations

from typing import Any

from src.core.data_store import DataStore
from src.core.schema_graph import SchemaGraph


class ReferenceResolver:
    """Caches `(entity, sys_id) -> record` lookups across an execution.

    Dangling references (pointing at records that don't exist) return
    None from `resolve` and a `[Unknown ...]` placeholder from
    `display_name_of`. The executor logs a warning on the trace.
    """

    def __init__(self, graph: SchemaGraph, store: DataStore) -> None:
        self._graph = graph
        self._store = store
        self._cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    def resolve(self, entity: str, sys_id: str) -> dict[str, Any] | None:
        key = (entity, sys_id)
        if key in self._cache:
            return self._cache[key]
        rec = self._store.get(entity, sys_id)
        self._cache[key] = rec
        return rec

    def display_name_of(self, entity: str, sys_id: str) -> str:
        rec = self.resolve(entity, sys_id)
        if rec is None:
            return f"[Unknown {entity} {sys_id}]"
        if entity == "sys_user":
            return str(rec.get("name", f"User {sys_id}"))
        if entity == "sys_user_group":
            return str(rec.get("name", f"Group {sys_id}"))
        if entity == "incident":
            return str(rec.get("number", f"Incident {sys_id}"))
        if entity == "kb_knowledge":
            return str(rec.get("number", f"Article {sys_id}"))
        if entity == "category":
            return str(rec.get("name", sys_id))
        return f"[{entity} {sys_id}]"

    def cache_size(self) -> int:
        return len(self._cache)

    def cache_hits(self) -> int:
        return self._hit_count

    _hit_count: int = 0
