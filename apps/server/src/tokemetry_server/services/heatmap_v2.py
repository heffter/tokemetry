"""Provider-neutral activity heatmap over the v2 ledger (Task 74, Gap 1).

Builds the weekday-by-hour punch card and the daily contribution calendar from
``usage_events_v2`` final attempts, honoring the uniform v2 dimension filters
(so BreakdownsView's heatmaps obey the global provider/model filter, unlike the
v1 ``/api/v1/heatmap``). Bucketing is done in Python over the bounded range for
dialect portability (no dialect-specific date functions).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.query_framework import QueryFilters


@dataclass(frozen=True)
class PunchCell:
    """One weekday-by-hour cell: weekday 0=Monday..6=Sunday, hour 0..23 (UTC)."""

    weekday: int
    hour: int
    value: int


@dataclass(frozen=True)
class CalendarCell:
    """One day's token total for the contribution calendar."""

    day: date
    value: int


@dataclass(frozen=True)
class HeatmapV2:
    """The punch card, calendar, and range metadata."""

    punch_card: list[PunchCell]
    calendar: list[CalendarCell]
    total_tokens: int
    start: date
    end: date


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def build_heatmap(
    session: AsyncSession,
    filters: QueryFilters,
    start: date,
    end: date,
) -> HeatmapV2:
    """Aggregate filtered final-attempt tokens into a heatmap over [start, end]."""
    event = models.UsageEventV2
    start_ts = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_ts = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)
    token_total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
        + event.reasoning_tokens
    )
    statement = select(event.ts_started, token_total).where(
        event.event_kind == "attempt",
        event.finality == "final",
        event.ts_started >= start_ts,
        event.ts_started <= end_ts,
    )
    # Uniform v2 dimension filters (the same columns the grouped queries honor).
    dimension_filters = {
        event.provider: filters.provider,
        event.native_model: filters.native_model,
        event.machine: filters.machine,
        event.project: filters.project,
        event.environment: filters.environment,
    }
    for column, value in dimension_filters.items():
        if value is not None:
            statement = statement.where(column == value)
    if filters.outcome is not None:
        statement = statement.where(
            event.success.is_(filters.outcome == "success")
        )

    rows = (await session.execute(statement)).all()
    punch: dict[tuple[int, int], int] = {}
    calendar: dict[date, int] = {}
    total = 0
    for ts, tokens in rows:
        aware = _as_utc(ts)
        amount = int(tokens or 0)
        total += amount
        punch_key = (aware.weekday(), aware.hour)
        punch[punch_key] = punch.get(punch_key, 0) + amount
        day = aware.date()
        calendar[day] = calendar.get(day, 0) + amount

    return HeatmapV2(
        punch_card=[
            PunchCell(weekday=wd, hour=hr, value=value)
            for (wd, hr), value in sorted(punch.items())
        ],
        calendar=[
            CalendarCell(day=day, value=value)
            for day, value in sorted(calendar.items())
        ],
        total_tokens=total,
        start=start,
        end=end,
    )
