from __future__ import annotations

from typing import Any

from src.llm.base import (
    BaseLLMClient,
    LLMError,
    LLMRateLimitError,
    LLMToolCallMissingError,
    LLMTransientError,
)
from src.llm.retry import with_retry


class AnthropicClient(BaseLLMClient):
    """Anthropic provider. Uses the async `anthropic.AsyncAnthropic` client.

    `tool_call` forces a single named tool via `tool_choice={"type": "tool",
    "name": ...}`; we then extract the tool_use content block and return
    its `input` dict.
    """

    def __init__(self, model: str, api_key: str | None) -> None:
        super().__init__(model)
        # Imported lazily so test files can monkeypatch the client.
        from anthropic import AsyncAnthropic

        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self._client = AsyncAnthropic(api_key=api_key)

    async def tool_call(
        self,
        prompt: str,
        tool_schema: dict[str, Any],
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            try:
                resp = await self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system or "",
                    tools=[
                        {
                            "name": tool_schema["name"],
                            "description": tool_schema.get("description", ""),
                            "input_schema": tool_schema["input_schema"],
                        }
                    ],
                    tool_choice={"type": "tool", "name": tool_schema["name"]},
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:  # noqa: BLE001
                raise _classify(exc) from exc

            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    inp = getattr(block, "input", None)
                    if isinstance(inp, dict):
                        return dict(inp)
                    return {}
            raise LLMToolCallMissingError(
                f"Anthropic response contained no tool_use block for {tool_schema['name']!r}"
            )

        return await with_retry(_call)

    async def text_complete(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        async def _call() -> str:
            try:
                resp = await self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system or "",
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:  # noqa: BLE001
                raise _classify(exc) from exc

            parts: list[str] = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text = getattr(block, "text", "")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)

        return await with_retry(_call)


def _classify(exc: Exception) -> Exception:
    """Maps anthropic-specific exceptions onto our retry-aware categories.
    Falls through to the original exception if not recognised.
    """
    name = type(exc).__name__
    if name in {"RateLimitError"}:
        return LLMRateLimitError(str(exc))
    if name in {"APIConnectionError", "APITimeoutError", "InternalServerError"}:
        return LLMTransientError(str(exc))
    return exc
