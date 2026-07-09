"""Daily rollup refresh.

After events are ingested, the affected days' rollups are recomputed from
``usage_events`` and written to ``daily_rollups`` with
``provenance='derived'``. Recomputing whole days (rather than applying
deltas) is correct in the presence of keep-max updates: the aggregate always
reflects the current stored rows. A collector batch touches one or two days,
so the cost is small.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.db.upsert import daily_rollups_upsert

#: Provenance stamped on rollups computed from real events.
DERIVED = "derived"


async def refresh_rollups_for_days(
    session: AsyncSession,
    dialect_name: str,
    days: Iterable[date],
) -> int:
    """Recompute and upsert rollups for each given day; return rows written.

    Args:
        session: Active session (caller owns the transaction).
        dialect_name: Dialect for the upsert syntax.
        days: Days to recompute (typically the days touched by a batch).
    """
    written = 0
    for day in days:
        rows = await _aggregate_day(session, day)
        if rows:
            stmt = daily_rollups_upsert(dialect_name, models.DailyRollup.__table__, rows)
            await session.execute(stmt)
            written += len(rows)
    return written


async def _aggregate_day(session: AsyncSession, day: date) -> list[dict[str, object]]:
    """Aggregate usage_events for one day into rollup row dicts.

    Grouped by ``(provider, machine, model, project)`` with ``''`` sentinels
    for null machine/project so the grain matches ``daily_rollups``.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    event = models.UsageEvent
    machine = func.coalesce(event.machine, "").label("machine")
    project = func.coalesce(event.project, "").label("project")
    total = func.sum(
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )

    statement = (
        select(
            event.provider,
            machine,
            event.model,
            project,
            func.sum(event.input_tokens),
            func.sum(event.output_tokens),
            func.sum(event.cache_read_tokens),
            func.sum(event.cache_write_short_tokens),
            func.sum(event.cache_write_long_tokens),
            total,
            func.sum(event.cost_usd),
        )
        .where(event.ts >= day_start, event.ts < day_end)
        .group_by(event.provider, machine, event.model, project)
    )

    result = await session.execute(statement)
    rows: list[dict[str, object]] = []
    for record in result.all():
        rows.append(
            {
                "day": day,
                "provider": record[0],
                "machine": record[1],
                "model": record[2],
                "project": record[3],
                "input_tokens": record[4] or 0,
                "output_tokens": record[5] or 0,
                "cache_read_tokens": record[6] or 0,
                "cache_write_short_tokens": record[7] or 0,
                "cache_write_long_tokens": record[8] or 0,
                "total_tokens": record[9] or 0,
                "cost_usd": record[10],
                "provenance": DERIVED,
            }
        )
    return rows
