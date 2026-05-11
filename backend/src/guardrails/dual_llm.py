from __future__ import annotations

# Defense 3 (Willison 2023): the LLM that plans never sees raw data; the LLM
# that responds sees data but cannot issue tool calls.
#
# The boundary is enforced by code structure, not at runtime — the planner
# imports a `BaseLLMClient` instance configured with the planner's tool
# schema, and the response generator uses `text_complete` with no tools at
# all. There is no "cross-pollination" point between the two.
#
# This module exposes a small assertion helper used by the API layer's
# DI container at startup, plus a marker class so tests can verify which
# role a client is meant for.
from dataclasses import dataclass

from src.llm.base import BaseLLMClient


@dataclass(frozen=True)
class DualLLMBoundary:
    """The two-client object the API layer uses. Constructed by `api/deps.py`
    at startup and never mutated."""

    privileged: BaseLLMClient  # planner: preprocessor + plan generator
    quarantined: BaseLLMClient  # response generator only

    def assert_distinct(self) -> None:
        """Cheap structural check — privileged and quarantined MUST be
        distinct objects so a future refactor can't accidentally collapse
        them into one."""
        if self.privileged is self.quarantined:
            raise RuntimeError(
                "DualLLMBoundary: privileged and quarantined must be "
                "separate client instances (Defense 3)"
            )
