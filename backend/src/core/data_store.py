from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel

FilterOperator = Literal[
    "eq",
    "neq",
    "in",
    "contains",
    "gt",
    "lt",
    "gte",
    "lte",
    "is_null",
    "is_not_null",
]

FilterValue = str | int | bool | list[str | int] | None


class Filter(BaseModel):
    """A single field filter. `field` may be plain (`"state"`) or
    entity-qualified (`"incident.state"`); the store normalises either form.

    For value-mapped fields the store accepts both display strings and integer
    codes — it translates display → code via the SchemaGraph at match time.
    """

    field: str
    operator: FilterOperator
    value: FilterValue = None
    case_sensitive: bool = False


Record = dict[str, Any]


class DataStore(Protocol):
    """The contract executor + write_path use to talk to data.

    The in-memory implementation is in `in_memory_store.py`. A future
    `servicenow_rest_store.py` would implement the same protocol against
    the real ServiceNow Table API; the executor never sees the difference.
    """

    def find(
        self, entity: str, filters: list[Filter], limit: int = 100
    ) -> list[Record]: ...

    def get(self, entity: str, sys_id: str) -> Record | None: ...

    def get_many(self, entity: str, sys_ids: list[str]) -> list[Record]: ...

    def count(self, entity: str, filters: list[Filter]) -> int: ...

    def create(self, entity: str, fields: dict[str, Any]) -> Record: ...

    def update(self, entity: str, sys_id: str, fields: dict[str, Any]) -> Record: ...
