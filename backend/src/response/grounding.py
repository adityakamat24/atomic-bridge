from __future__ import annotations

import re
from typing import Any

# Patterns the response should never invent — they must appear verbatim in the
# execution data if they appear in the response text. Loose by design: the
# false-positive cost is a warning, not a refusal.
INC_NUMBER_RE = re.compile(r"INC\d{6,}")
KB_NUMBER_RE = re.compile(r"KB\d{6,}")
SYS_ID_RE = re.compile(r"\b(?:usr|inc|grp|kb)-?[a-f0-9]{6,}\b", re.IGNORECASE)


def check_grounding(response: str, data: Any) -> list[str]:
    """Returns warnings for entity references in the response that don't
    appear anywhere in the data.

    Used by the API layer alongside the output filter. Warnings get attached
    to the audit-log entry and surfaced via the trace, not as a refusal.
    """
    serialised = _flatten(data)
    warnings: list[str] = []

    for kind, pattern in (
        ("incident number", INC_NUMBER_RE),
        ("KB article", KB_NUMBER_RE),
        ("sys_id", SYS_ID_RE),
    ):
        for match in pattern.findall(response):
            if match not in serialised:
                warnings.append(
                    f"ungrounded {kind} {match!r} appears in response "
                    f"but not in execution data"
                )
    return warnings


def _flatten(value: Any) -> str:
    """Cheap stringification. Good enough for substring matching; we don't
    need round-trippable JSON."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_flatten(v) for v in value)
    if isinstance(value, dict):
        return " ".join(f"{k}={_flatten(v)}" for k, v in value.items())
    return str(value)
