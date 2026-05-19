from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.core.schema_graph import SchemaGraph

if TYPE_CHECKING:
    from src.guardrails.view_scope import ViewScope


class SessionContext(BaseModel):
    can_view_pii: bool = False
    can_write: bool = True


_LEAK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9]{24,}"), "potential API key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "potential AWS key"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN-shaped string"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "long hex string (possible sys_id leak)"),
)

REDACTION = "[REDACTED]"


class OutputFilter:
    """Per-record PII redaction + final-text regex leak scan."""

    def __init__(self, graph: SchemaGraph, session: SessionContext) -> None:
        self._graph = graph
        self._session = session
        self._sensitive: dict[str, set[str]] = {}
        for entity in graph.all_entities():
            self._sensitive[entity.id] = {
                f.name for f in graph.fields_of(entity.id) if f.is_sensitive
            }

    @classmethod
    def from_view_scope(
        cls, graph: SchemaGraph, scope: ViewScope,
    ) -> OutputFilter:
        return cls(graph, SessionContext(can_view_pii=scope.can_view_pii))

    def filter_record(self, record: dict[str, Any], entity: str) -> dict[str, Any]:
        if self._session.can_view_pii:
            return self._strip_sys_ids(record)
        sensitive = self._sensitive.get(entity, set())
        out: dict[str, Any] = {}
        for k, v in record.items():
            if k in sensitive:
                out[k] = REDACTION
            elif _is_sys_id_field(k):
                continue
            else:
                out[k] = v
        return out

    def scan_response_text(self, text: str) -> tuple[str, list[str]]:
        warnings: list[str] = []
        out = text
        for pattern, msg in _LEAK_PATTERNS:
            if pattern.search(out):
                warnings.append(msg)
                out = pattern.sub(REDACTION, out)
        return out, warnings

    def _strip_sys_ids(self, record: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in record.items() if not _is_sys_id_field(k)}


def _is_sys_id_field(name: str) -> bool:
    return name == "sys_id" or name.endswith("_sys_id")
