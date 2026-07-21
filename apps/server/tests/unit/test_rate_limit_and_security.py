"""Unit coverage for the rate limiter's Retry-After and security helpers (Task 70.5)."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.requests import Request
from tokemetry_server.api.security import (
    is_api_path,
    is_health_path,
    is_ingest_path,
    rate_key,
)
from tokemetry_server.services.rate_limit import RateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_check_allows_until_empty_then_reports_retry_after() -> None:
    clock = _Clock()
    limiter = RateLimiter(capacity=2, refill_per_second=1.0, clock=clock)
    assert limiter.check("k") is None
    assert limiter.check("k") is None
    # Bucket empty: next call denied with the wait until one token refills.
    retry = limiter.check("k")
    assert retry is not None
    assert abs(retry - 1.0) < 1e-9


def test_check_refills_over_time() -> None:
    clock = _Clock()
    limiter = RateLimiter(capacity=1, refill_per_second=2.0, clock=clock)
    assert limiter.check("k") is None
    assert limiter.check("k") is not None  # empty
    clock.now = 0.5  # one token refilled at 2/s
    assert limiter.check("k") is None


def test_allow_delegates_to_check() -> None:
    limiter = RateLimiter(capacity=1, refill_per_second=0.0)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False


def test_path_classification() -> None:
    assert is_api_path("/api/v2/usage")
    assert not is_api_path("/dashboard")
    assert is_ingest_path("/api/v2/ingest/events")
    assert not is_ingest_path("/api/v2/usage")
    assert is_health_path("/api/v1/health")
    assert not is_health_path("/api/v2/usage")


def _request(headers: dict[str, str], host: str | None = "1.2.3.4") -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "headers": Headers(headers).raw,
        "client": (host, 1234) if host else None,
    }
    return Request(scope)


def test_rate_key_prefers_bearer_token() -> None:
    assert rate_key(_request({"authorization": "Bearer abc123"})) == "token:abc123"


def test_rate_key_falls_back_to_client_host() -> None:
    assert rate_key(_request({})) == "host:1.2.3.4"
    assert rate_key(_request({}, host=None)) == "host:unknown"
