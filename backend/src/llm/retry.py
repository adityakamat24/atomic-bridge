from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from src.llm.base import LLMRateLimitError, LLMTransientError


async def with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
    jitter: float = 0.25,
) -> T:
    """Exponential backoff for rate-limit and transient errors.

    Only `LLMRateLimitError` and `LLMTransientError` are retried. All other
    exceptions propagate immediately (especially `LLMToolCallMissingError` and
    user-facing validation errors).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except (LLMRateLimitError, LLMTransientError):
            if attempt >= max_attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, jitter * delay)
            await asyncio.sleep(delay)
