"""Unit tests for the in-process token-bucket rate limiter."""

from __future__ import annotations

from tokemetry_server.services.rate_limit import RateLimiter


class _Clock:
    """A manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_capacity_then_blocks() -> None:
    clock = _Clock()
    limiter = RateLimiter(capacity=3, refill_per_second=0, clock=clock)
    assert [limiter.allow("k") for _ in range(4)] == [True, True, True, False]


def test_refills_over_time() -> None:
    clock = _Clock()
    limiter = RateLimiter(capacity=2, refill_per_second=1.0, clock=clock)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False
    clock.now = 1.0  # one token refilled
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False


def test_capacity_caps_refill() -> None:
    clock = _Clock()
    limiter = RateLimiter(capacity=2, refill_per_second=100.0, clock=clock)
    limiter.allow("k")
    limiter.allow("k")
    clock.now = 10.0  # would refill 1000 tokens, but capacity caps at 2
    assert [limiter.allow("k") for _ in range(3)] == [True, True, False]


def test_keys_are_independent() -> None:
    clock = _Clock()
    limiter = RateLimiter(capacity=1, refill_per_second=0, clock=clock)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True  # separate bucket
    assert limiter.allow("a") is False
