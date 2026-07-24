"""
Sorachio-STS Rate Limiter
Sliding window rate limiter for protecting the pipeline from rapid-fire inputs.

Features:
  - Sliding window algorithm (no burst spikes)
  - Thread-safe for async usage
  - Configurable window size and max requests
  - Graceful degradation (reject excess, don't crash)
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from utils.logging_setup import get_logger

log = get_logger("utils.rate_limiter")


class RateLimiter:
    """
    Sliding window rate limiter.

    Tracks request timestamps within a sliding window and rejects
    requests when the limit is exceeded.
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: float = 60.0,
    ):
        """
        Initialize rate limiter.

        Args:
            max_requests: Maximum requests allowed within the window.
            window_seconds: Sliding window duration in seconds.
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

        log.info(
            f"[RateLimiter] Initialized — "
            f"max={max_requests} requests per {window_seconds:.1f}s"
        )

    async def allow(self) -> bool:
        """
        Check if a request is allowed under the rate limit.

        Returns:
            True if request is allowed, False if rate limit exceeded.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds

            # Remove timestamps outside the window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            # Check if under limit
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True

            # Rate limit exceeded
            log.debug(
                f"[RateLimiter] Rate limit exceeded — "
                f"{len(self._timestamps)}/{self.max_requests} "
                f"requests in {self.window_seconds:.1f}s window"
            )
            return False

    async def wait(self) -> bool:
        """
        Wait until a request can be allowed (up to window_seconds).

        Returns:
            True if request was eventually allowed, False if timed out.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds

            # Remove timestamps outside the window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            # If under limit, allow immediately
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True

            # Calculate wait time until oldest request expires
            wait_time = self._timestamps[0] + self.window_seconds - now

        # Wait outside the lock
        if wait_time > 0:
            log.debug(f"[RateLimiter] Waiting {wait_time:.2f}s for rate limit")
            await asyncio.sleep(wait_time)

        # Try again after waiting
        return await self.allow()

    def get_status(self) -> dict:
        """Return current rate limiter status."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Count requests in current window
        active = sum(1 for t in self._timestamps if t >= cutoff)

        return {
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "current_requests": active,
            "remaining": max(0, self.max_requests - active),
        }
