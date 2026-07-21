"""Authoritative server-side cache-savings computation (Task 74, Gap 2).

Replaces the dashboard's client-side pricing math for the "Caching saved" tile:
for each priced (provider, native_model), the cache-read tokens would otherwise
have cost the full input rate, so the saving is
``cache_read_tokens * (input_rate - cache_read_rate)``. Uses the same rate-card
resolution as the cost engine, so the figure is authoritative rather than a
best-effort client estimate. Honors the uniform v2 dimension filters.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.pricing_v2 import resolve_rate
from tokemetry_server.services.query_framework import QueryFilters

_INPUT_UNIT = "input_token"
_CACHE_READ_UNIT = "cache_read_token"


async def cache_savings_usd(
    session: AsyncSession,
    filters: QueryFilters,
    start: date,
    end: date,
) -> Decimal:
    """Total USD saved by cache reads over [start, end] under the filters."""
    event = models.UsageEventV2
    start_ts = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_ts = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)
    statement = (
        select(
            event.provider,
            event.native_model,
            func.coalesce(func.sum(event.cache_read_tokens), 0),
        )
        .where(
            event.event_kind == "attempt",
            event.finality == "final",
            event.ts_started >= start_ts,
            event.ts_started <= end_ts,
        )
        .group_by(event.provider, event.native_model)
    )
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

    rows = (await session.execute(statement)).all()
    total = Decimal(0)
    for provider, native_model, cache_read in rows:
        tokens = int(cache_read or 0)
        if tokens == 0:
            continue
        input_rate = await resolve_rate(
            session, provider, native_model, _INPUT_UNIT, end
        )
        cache_rate = await resolve_rate(
            session, provider, native_model, _CACHE_READ_UNIT, end
        )
        if input_rate is None or cache_rate is None:
            continue  # unpriced -> no authoritative saving to claim
        delta = input_rate.unit_price - cache_rate.unit_price
        if delta > 0:
            total += Decimal(tokens) * delta
    return total
