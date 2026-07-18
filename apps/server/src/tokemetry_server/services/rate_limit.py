"""In-process token-bucket rate limiting (FR-INGEST-015).

A minimal, dependency-free limiter that keeps ingest and query traffic in
separate buckets so a burst of ingest never starves query reads (and vice
versa). Each caller key (token label) gets its own bucket that refills at a
steady rate up to a burst capacity. This is deliberately simple -- single
process, in memory -- and is hardened (shared store, sliding windows) in Task
70; it exists now so the two traffic classes are already distinguished.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """A per-key token bucket shared by one traffic class."""

    def __init__(
        self,
        capacity: float,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the limiter.

        Args:
            capacity: Burst size -- the most tokens a key may hold.
            refill_per_second: Steady-state token refill rate.
            clock: Monotonic time source in seconds (injected for tests).
        """
        self._capacity = capacity
        self._refill = refill_per_second
        self._clock = clock
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        """Consume ``cost`` tokens for ``key``; return whether it was allowed.

        A key first seen starts full. Tokens accrue at the refill rate since the
        last call, capped at capacity; the request is allowed only if enough
        tokens remain, in which case they are deducted.
        """
        now = self._clock()
        tokens, last = self._state.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._refill)
        if tokens < cost:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - cost, now)
        return True
