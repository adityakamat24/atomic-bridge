from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class RateLimitExceededError(Exception):
    """Raised when a key has exceeded its allowance for the current window."""


class TokenBucketRateLimiter:
    """Sliding-window-ish limiter: keeps a deque of request timestamps per
    key and rejects if more than `per_minute` fit inside the trailing 60s.

    Production swap: Redis-backed (e.g., `redis-rate-limiter`).
    """

    def __init__(self, per_minute: int = 60) -> None:
        self._per_minute = per_minute
        self._window = 60.0
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        """Records a request and raises if over budget."""
        async with self._lock:
            now = time.time()
            bucket = self._buckets[key]
            cutoff = now - self._window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._per_minute:
                raise RateLimitExceededError(
                    f"rate limit exceeded for {key!r} ({self._per_minute}/min)"
                )
            bucket.append(now)

    def reset(self) -> None:
        self._buckets.clear()
