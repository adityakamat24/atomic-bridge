from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMError(Exception):
    """Base for all LLM client errors."""


class LLMRateLimitError(LLMError):
    """The provider returned a rate-limit response. Retryable."""


class LLMTransientError(LLMError):
    """A transient backend / network error. Retryable."""


class LLMToolCallMissingError(LLMError):
    """The model returned text instead of a tool call when tool_call() was asked
    to force one. The planner treats this as a hard validation failure rather
    than retry blindly (see 03-planner.md guardrails)."""


class BaseLLMClient(ABC):
    """The provider-agnostic surface used by the planner and the response
    generator. Two methods only:

    - `tool_call(prompt, tool_schema, system)` forces the model to invoke
      a single named tool and returns its argument dict.
    - `text_complete(prompt, system)` plain text generation.

    Async-only — all callers are inside FastAPI handlers.
    """

    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    async def tool_call(
        self,
        prompt: str,
        tool_schema: dict[str, Any],
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Force a tool invocation. `tool_schema` is the provider-neutral form:

            {"name": "...", "description": "...", "input_schema": {...}}

        Returns the tool's `input` dict.
        """

    @abstractmethod
    async def text_complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """Plain text completion. Returns the model's text response."""
