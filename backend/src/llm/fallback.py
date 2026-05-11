"""Cross-provider LLM fallback.

Wraps a primary `BaseLLMClient` and a fallback. The primary already retries
rate-limit and transient errors with exponential backoff (see `retry.py`).
When that retry loop gives up, or when any other `LLMError` reaches us
(persistent quota exhausted, API outage, malformed responses), we call the
fallback once. If the fallback also fails, the error propagates so the
caller can return a clean 5xx.

Why a separate class instead of folding it into the existing retry: retry
is intra-provider (same model, same endpoint, same likely failure mode).
Fallback is cross-provider (different model, different endpoint, different
failure surface). Keeping them separate means a 30-second Anthropic outage
doesn't burn three retry attempts before the OpenAI request fires.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm.base import BaseLLMClient, LLMError, LLMToolCallMissingError

logger = logging.getLogger(__name__)


class FallbackLLMClient(BaseLLMClient):
    """Composite LLM client. Try primary. On `LLMError`, call fallback once.

    `LLMToolCallMissingError` is NOT failed over: it means the primary returned
    text instead of a tool call, which is a planner-validation issue, not a
    provider issue. Failing over to a different model is unlikely to help and
    masks a real bug in the planner prompt.
    """

    def __init__(self, primary: BaseLLMClient, fallback: BaseLLMClient) -> None:
        super().__init__(model=f"{primary.model}+fallback:{fallback.model}")
        self._primary = primary
        self._fallback = fallback

    async def tool_call(
        self,
        prompt: str,
        tool_schema: dict[str, Any],
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        try:
            return await self._primary.tool_call(
                prompt, tool_schema, system=system,
                temperature=temperature, max_tokens=max_tokens,
            )
        except LLMToolCallMissingError:
            raise
        except LLMError as exc:
            logger.warning(
                "primary_llm_failed_falling_back",
                extra={"primary": self._primary.model, "fallback": self._fallback.model, "error": str(exc)},
            )
            return await self._fallback.tool_call(
                prompt, tool_schema, system=system,
                temperature=temperature, max_tokens=max_tokens,
            )

    async def text_complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        try:
            return await self._primary.text_complete(
                prompt, system=system,
                temperature=temperature, max_tokens=max_tokens,
            )
        except LLMError as exc:
            logger.warning(
                "primary_llm_failed_falling_back",
                extra={"primary": self._primary.model, "fallback": self._fallback.model, "error": str(exc)},
            )
            return await self._fallback.text_complete(
                prompt, system=system,
                temperature=temperature, max_tokens=max_tokens,
            )
