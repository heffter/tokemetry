"""Timing harness for the v2 query and rollup NFRs (Task 66.8).

Measures the operations the performance NFRs cover -- 30-day aggregated usage and
cost queries (NFR-PERF-003, p95 < 500 ms on reference hardware), a bounded
attempt page, and a full-day rollup refresh (FR-ROLLUP-012) -- reporting the best
of a few runs per operation. The reference-hardware figures are produced against
Postgres and recorded in the performance doc; the smoke test asserts correctness,
not wall-clock, so CI stays stable.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.services.queries_v2 import grouped_costs, grouped_usage
from tokemetry_server.services.query_framework import QueryFilters
from tokemetry_server.services.rollups import refresh_rollups_for_days
from tokemetry_server.services.trace_queries import list_attempts

#: The reference window a 30-day query covers within the seeded 90-day span.
_WINDOW_END = datetime(2026, 3, 1, tzinfo=UTC)


async def _best(operation: Callable[[], Awaitable[object]], repeats: int = 3) -> float:
    """Return the fastest of ``repeats`` runs of ``operation`` in seconds."""
    best = float("inf")
    for _ in range(repeats):
        started = time.perf_counter()
        await operation()
        best = min(best, time.perf_counter() - started)
    return best


async def run_benchmarks(
    session: AsyncSession, dialect_name: str, window_days: int = 30
) -> dict[str, float]:
    """Time the NFR-relevant query and rollup operations; return seconds each."""
    end = _WINDOW_END
    start = end - timedelta(days=window_days)
    no_filters = QueryFilters()
    return {
        "usage_30d_by_provider": await _best(
            lambda: grouped_usage(session, "provider", start, end, no_filters)
        ),
        "costs_30d_by_provider": await _best(
            lambda: grouped_costs(session, "provider", start, end, no_filters)
        ),
        "attempts_page_50": await _best(
            lambda: list_attempts(session, start, end, no_filters, None, None, 50)
        ),
        "rollup_refresh_1day": await _best(
            lambda: refresh_rollups_for_days(session, dialect_name, [start.date()]),
            repeats=1,
        ),
    }
