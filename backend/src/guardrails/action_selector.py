from __future__ import annotations

# Defense 1 (Beurer-Kellner et al. 2025): the LLM cannot execute arbitrary
# tool calls — it picks from a pre-approved set.
#
# The actual enforcement happens at TWO layers:
#   1. The LLM's structured-output / tool-use schema — the provider-level
#      `tool_choice` forces a single named tool.
#   2. The Pydantic discriminated union in `planner/plan_schema.Operation`
#      rejects any unknown `op` value at parse time.
#
# This module is a third, redundant layer used by tests and audit code to
# state the invariant in human-readable form.

ALLOWED_OPS: frozenset[str] = frozenset(
    {
        "find",
        "traverse",
        "aggregate",
        "kb_lookup",
        "resolve",
        "write_proposal",
    }
)

ALLOWED_INTENTS: frozenset[str] = frozenset(
    {
        "lookup",
        "knowledge",
        "analytical",
        "cross_reference",
        "write_proposal",
        "ambiguous",
        "out_of_scope",
    }
)


class ActionSelectorViolation(Exception):  # noqa: N818  -- name fixed by spec
    """Raised by the action selector if a non-allowlisted op or intent slips
    past the Pydantic schema (should be unreachable in practice)."""


def assert_op_allowed(op_name: str) -> None:
    if op_name not in ALLOWED_OPS:
        raise ActionSelectorViolation(
            f"op {op_name!r} is not in the action selector's allowed set"
        )


def assert_intent_allowed(intent: str) -> None:
    if intent not in ALLOWED_INTENTS:
        raise ActionSelectorViolation(
            f"intent {intent!r} is not in the action selector's allowed set"
        )
