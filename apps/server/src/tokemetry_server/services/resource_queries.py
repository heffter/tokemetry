"""Read queries for the remaining v2 resources (TOK-7, FR-LIMIT-004).

Keyset-paginated listings of limit snapshots (with official/estimated
provenance), data-quality events (filterable by kind/subject/source/resolved,
feeding the Task 67 page), and the daily rollup rows exposed directly for
external tooling with a stable column contract. All read-only, scope
``query:read`` at the endpoint layer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.query_framework import (
    Page,
    build_page,
    decode_cursor,
    encode_cursor,
    keyset_condition,
)


def _apply_ts_keyset(
    statement: Select[Any], ts_col: Any, id_col: Any, cursor: str | None
) -> Select[Any]:
    """Apply a newest-first ``(ts, id)`` keyset condition from an opaque cursor."""
    if cursor is None:
        return statement
    value, row_id = decode_cursor(cursor)
    return statement.where(
        keyset_condition(ts_col, id_col, datetime.fromisoformat(str(value)), row_id, True)
    )


async def list_limits(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    provider: str | None,
    machine: str | None,
    window_kind: str | None,
    provenance: str | None,
    cursor: str | None,
    limit: int,
) -> Page:
    """Newest-first limit snapshots over a range, filterable by grain."""
    lim = models.LimitSnapshot
    statement = select(lim).where(lim.ts >= start, lim.ts <= end)
    for column, value in (
        (lim.provider, provider), (lim.machine, machine),
        (lim.window_kind, window_kind), (lim.provenance, provenance),
    ):
        if value is not None:
            statement = statement.where(column == value)
    statement = _apply_ts_keyset(statement, lim.ts, lim.id, cursor)
    statement = statement.order_by(lim.ts.desc(), lim.id.desc()).limit(limit + 1)
    rows = list((await session.execute(statement)).scalars())
    return build_page(rows, limit, lambda r: encode_cursor(r.ts, r.id))


async def list_data_quality(
    session: AsyncSession,
    kind: str | None,
    subject: str | None,
    source: str | None,
    resolved: bool | None,
    cursor: str | None,
    limit: int,
) -> Page:
    """Newest-first data-quality events, filterable by kind/subject/source/state."""
    dq = models.DataQualityEvent
    statement = select(dq)
    for column, value in (
        (dq.kind, kind), (dq.subject, subject), (dq.source_id, source),
    ):
        if value is not None:
            statement = statement.where(column == value)
    if resolved is not None:
        statement = statement.where(dq.resolved.is_(resolved))
    statement = _apply_ts_keyset(statement, dq.ts, dq.id, cursor)
    statement = statement.order_by(dq.ts.desc(), dq.id.desc()).limit(limit + 1)
    rows = list((await session.execute(statement)).scalars())
    return build_page(rows, limit, lambda r: encode_cursor(r.ts, r.id))


async def list_rollups(
    session: AsyncSession,
    day_from: date,
    day_to: date,
    provider: str | None,
    model: str | None,
    machine: str | None,
    source: str | None,
    environment: str | None,
    billing_mode: str | None,
    cursor: str | None,
    limit: int,
) -> Page:
    """Daily rollup rows over a day range for external tooling, keyset-paginated."""
    rollup = models.DailyRollup
    statement = select(rollup).where(rollup.day >= day_from, rollup.day <= day_to)
    for column, value in (
        (rollup.provider, provider), (rollup.model, model), (rollup.machine, machine),
        (rollup.source, source), (rollup.environment, environment),
        (rollup.billing_mode, billing_mode),
    ):
        if value is not None:
            statement = statement.where(column == value)
    if cursor is not None:
        value_raw, row_id = decode_cursor(cursor)
        statement = statement.where(
            keyset_condition(
                rollup.day, rollup.id, date.fromisoformat(str(value_raw)), row_id, False
            )
        )
    statement = statement.order_by(rollup.day.asc(), rollup.id.asc()).limit(limit + 1)
    rows = list((await session.execute(statement)).scalars())
    return build_page(rows, limit, lambda r: encode_cursor(r.day, r.id))
