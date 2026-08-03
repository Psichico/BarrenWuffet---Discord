"""
A small thread-safe token-bucket rate limiter.

WHY: Alpha Vantage enforces a per-minute call budget (we planned 75/min). Every
provider HTTP call goes through one shared limiter so we never exceed the budget
and get throttled. This is a proactive guardrail; retry/backoff (in the provider)
is the reactive backstop.

The bucket holds up to `per_minute` tokens and refills continuously at
`per_minute / 60` tokens per second. acquire() blocks until a token is free.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Token-bucket limiter. Safe to share across threads."""

    def __init__(self, per_minute: int) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        self._capacity = float(per_minute)
        self._tokens = float(per_minute)          # start full
        self._refill_per_sec = per_minute / 60.0
        self._lock = threading.Lock()
        self._last = time.monotonic()

    def _refill(self) -> None:
        """Add tokens accrued since the last check (caller holds the lock)."""
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_sec)

    def acquire(self) -> None:
        """
        Block until one token is available, then consume it.

        Uses short sleeps in a loop rather than precise scheduling — simple and
        good enough for a per-minute budget.
        """
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # How long until at least one token accrues?
                deficit = 1.0 - self._tokens
                wait = deficit / self._refill_per_sec
            # Sleep OUTSIDE the lock so other threads can make progress.
            time.sleep(min(wait, 1.0))
