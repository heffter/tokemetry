"""Functional smoke test for the benchmark harness (Task 66.8).

Seeds a small dataset so the harness stays correct in CI: the generator produces
final attempts across providers, the timed queries return the expected shape, and
every measured operation completes. Wall-clock is not asserted (that is measured
on reference hardware and recorded in docs/architecture/performance.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.services.queries_v2 import grouped_usage
from tokemetry_server.services.query_framework import QueryFilters

from .benchmark import run_benchmarks
from .dataset import generate_attempts


async def test_generator_seeds_multi_provider_final_attempts(
    async_session: AsyncSession,
) -> None:
    seeded = await generate_attempts(async_session, 400, days=90)
    await async_session.commit()
    assert seeded == 400

    end = datetime(2026, 3, 1, tzinfo=UTC)
    start = end - timedelta(days=30)
    rows = await grouped_usage(async_session, "provider", start, end, QueryFilters())
    # The 30-day window spans several providers with non-zero usage.
    assert {r.key for r in rows} <= {"anthropic", "openai", "zai"}
    assert rows and all(r.attempt_count > 0 for r in rows)


async def test_benchmark_harness_runs_all_operations(
    async_session: AsyncSession,
) -> None:
    await generate_attempts(async_session, 400, days=90)
    await async_session.commit()

    results = await run_benchmarks(async_session, "sqlite")
    assert set(results) == {
        "usage_30d_by_provider", "costs_30d_by_provider",
        "attempts_page_50", "rollup_refresh_1day",
    }
    assert all(seconds >= 0.0 for seconds in results.values())
