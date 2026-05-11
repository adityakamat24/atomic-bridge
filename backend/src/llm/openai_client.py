from __future__ import annotations

import json
from typing import Any

from src.llm.base import (
    BaseLLMClient,
    LLMError,
    LLMRateLimitError,
    LLMToolCallMissingError,
    LLMTransientError,
)
from src.llm.retry import with_retry


class OpenAIClient(BaseLLMClient):
    """OpenAI provider. Uses the async `openai.AsyncOpenAI` client.

    `tool_call` uses the chat-completions tools API with
    `tool_choice={"type":"function", "function":{"name":...}}` to force
    a single function invocation; the function's argument JSON is parsed
    into a dict.
    """

    def __init__(self, model: str, api_key: str | None) -> None:
        super().__init__(model)
        from openai import AsyncOpenAI

        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        self._client = AsyncOpenAI(api_key=api_key)

    async def tool_call(
        self,
        prompt: str,
        tool_schema: dict[str, Any],
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            try:
                resp = await self._client.chat.completions.create(  # type: ignore[call-overload]
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    messages=messages,
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": tool_schema["name"],
                                "description": tool_schema.get("description", ""),
                                "parameters": tool_schema["input_schema"],
                            },
                        }
                    ],
                    tool_choice={
                        "type": "function",
                        "function": {"name": tool_schema["name"]},
                    },
                )
            except Exception as exc:  # noqa: BLE001
                raise _classify(exc) from exc

            choice = resp.choices[0]
            tool_calls = getattr(choice.message, "tool_calls", None) or []
            for tc in tool_calls:
                if tc.function.name == tool_schema["name"]:
                    args = tc.function.arguments
                    return dict(json.loads(args)) if args else {}
            raise LLMToolCallMissingError(
                f"OpenAI response contained no tool call for {tool_schema['name']!r}"
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
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    messages=messages,  # type: ignore[arg-type]
                )
            except Exception as exc:  # noqa: BLE001
                raise _classify(exc) from exc
            return resp.choices[0].message.content or ""

        return await with_retry(_call)


def _classify(exc: Exception) -> Exception:
    name = type(exc).__name__
    if name in {"RateLimitError"}:
        return LLMRateLimitError(str(exc))
    if name in {"APIConnectionError", "APITimeoutError", "InternalServerError"}:
        return LLMTransientError(str(exc))
    return exc
