"""Read-model aggregation queries for the dashboard and API.

Aggregations prefer the precomputed ``daily_rollups`` table for coarse
dimensions (day, provider, model, machine, project) and fall back to
``usage_events`` for fine dimensions (hour, session) where per-event detail
is needed. All functions are read-only and take an explicit time range.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.projects import DEFAULT_ROOTS, project_group

from tokemetry_server.db import models
from tokemetry_server.services.session_stats import SessionStats, compute_session_stats

#: Group-by dimensions backed by the daily_rollups table.
_ROLLUP_DIMENSIONS = {
    "provider": models.DailyRollup.provider,
    "model": models.DailyRollup.model,
    "machine": models.DailyRollup.machine,
    "project": models.DailyRollup.project,
}


@dataclass(frozen=True)
class UsageBucket:
    """One aggregated usage row for a group-by key."""

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    total_tokens: int
    cost_usd: Decimal | None


def _rollup_totals() -> list[object]:
    """Aggregate column expressions over daily_rollups token/cost sums."""
    rollup = models.DailyRollup
    return [
        func.coalesce(func.sum(rollup.input_tokens), 0),
        func.coalesce(func.sum(rollup.output_tokens), 0),
        func.coalesce(func.sum(rollup.cache_read_tokens), 0),
        func.coalesce(func.sum(rollup.cache_write_short_tokens), 0),
        func.coalesce(func.sum(rollup.cache_write_long_tokens), 0),
        func.coalesce(func.sum(rollup.total_tokens), 0),
        func.sum(rollup.cost_usd),
    ]


def _bucket_from_row(key: str, row: Sequence[Any]) -> UsageBucket:
    """Build a UsageBucket from a (key, *totals) result row."""
    return UsageBucket(
        key=key,
        input_tokens=int(row[0] or 0),
        output_tokens=int(row[1] or 0),
        cache_read_tokens=int(row[2] or 0),
        cache_write_short_tokens=int(row[3] or 0),
        cache_write_long_tokens=int(row[4] or 0),
        total_tokens=int(row[5] or 0),
        cost_usd=row[6] if row[6] is None else Decimal(str(row[6])),
    )


def _apply_rollup_filters(
    statement: Select[Any],
    start: date,
    end: date,
    provider: str | None,
    machine: str | None,
    model: str | None,
    project: str | None,
) -> Select[Any]:
    """Apply the common day-range and dimension filters to a rollup query."""
    rollup = models.DailyRollup
    statement = statement.where(rollup.day >= start, rollup.day <= end)
    if provider is not None:
        statement = statement.where(rollup.provider == provider)
    if machine is not None:
        statement = statement.where(rollup.machine == machine)
    if model is not None:
        statement = statement.where(rollup.model == model)
    if project is not None:
        statement = statement.where(rollup.project == project)
    return statement


async def usage_grouped(
    session: AsyncSession,
    group_by: str,
    start: date,
    end: date,
    provider: str | None = None,
    machine: str | None = None,
    model: str | None = None,
    project: str | None = None,
) -> list[UsageBucket]:
    """Aggregate usage over a day range grouped by one dimension.

    Supported ``group_by``: ``day``, ``provider``, ``model``, ``machine``,
    ``project`` (from daily_rollups), and ``session`` (from usage_events).

    Raises:
        ValueError: If ``group_by`` is not supported.
    """
    if group_by == "session":
        return await _usage_by_session(session, start, end, provider, machine, model, project)
    key_column: Any
    if group_by == "day":
        key_column = models.DailyRollup.day
    else:
        key_column = _ROLLUP_DIMENSIONS.get(group_by)
        if key_column is None:
            raise ValueError(f"unsupported group_by: {group_by}")

    statement: Select[Any] = (
        select(key_column, *_rollup_totals()).group_by(key_column).order_by(key_column)
    )
    statement = _apply_rollup_filters(statement, start, end, provider, machine, model, project)
    result = await session.execute(statement)
    return [_bucket_from_row(_key_to_str(row[0]), row[1:]) for row in result.all()]


async def _usage_by_session(
    session: AsyncSession,
    start: date,
    end: date,
    provider: str | None,
    machine: str | None,
    model: str | None,
    project: str | None,
) -> list[UsageBucket]:
    """Aggregate usage_events by session over a day range."""
    event = models.UsageEvent
    start_ts = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_ts = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    statement = (
        select(
            func.coalesce(event.session_id, ""),
            func.coalesce(func.sum(event.input_tokens), 0),
            func.coalesce(func.sum(event.output_tokens), 0),
            func.coalesce(func.sum(event.cache_read_tokens), 0),
            func.coalesce(func.sum(event.cache_write_short_tokens), 0),
            func.coalesce(func.sum(event.cache_write_long_tokens), 0),
            func.coalesce(func.sum(total), 0),
            func.sum(event.cost_usd),
        )
        .where(event.ts >= start_ts, event.ts <= end_ts)
        .group_by(func.coalesce(event.session_id, ""))
    )
    if provider is not None:
        statement = statement.where(event.provider == provider)
    if machine is not None:
        statement = statement.where(event.machine == machine)
    if model is not None:
        statement = statement.where(event.model == model)
    if project is not None:
        statement = statement.where(event.project == project)
    result = await session.execute(statement)
    return [_bucket_from_row(str(row[0]), row[1:]) for row in result.all()]


def _key_to_str(key: object) -> str:
    """Render a group-by key as a string (dates as ISO)."""
    if isinstance(key, date):
        return key.isoformat()
    return str(key)


@dataclass(frozen=True)
class Overview:
    """All-time totals for the dashboard summary strip."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    total_tokens: int
    cost_usd: Decimal | None
    session_count: int
    machine_count: int
    first_event: datetime | None
    last_event: datetime | None


async def overview(session: AsyncSession) -> Overview:
    """Return all-time token/cost totals and activity span across every event."""
    rollup = models.DailyRollup
    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(rollup.input_tokens), 0),
                func.coalesce(func.sum(rollup.output_tokens), 0),
                func.coalesce(func.sum(rollup.cache_read_tokens), 0),
                func.coalesce(func.sum(rollup.cache_write_short_tokens), 0),
                func.coalesce(func.sum(rollup.cache_write_long_tokens), 0),
                func.coalesce(func.sum(rollup.total_tokens), 0),
                func.sum(rollup.cost_usd),
            )
        )
    ).one()

    event = models.UsageEvent
    sessions = (
        await session.execute(
            select(func.count(func.distinct(event.session_id))).where(
                event.session_id.is_not(None)
            )
        )
    ).scalar_one()
    machines = (
        await session.execute(select(func.count(func.distinct(event.machine))))
    ).scalar_one()
    span = (await session.execute(select(func.min(event.ts), func.max(event.ts)))).one()

    return Overview(
        input_tokens=int(totals[0] or 0),
        output_tokens=int(totals[1] or 0),
        cache_read_tokens=int(totals[2] or 0),
        cache_write_short_tokens=int(totals[3] or 0),
        cache_write_long_tokens=int(totals[4] or 0),
        total_tokens=int(totals[5] or 0),
        cost_usd=None if totals[6] is None else Decimal(str(totals[6])),
        session_count=int(sessions or 0),
        machine_count=int(machines or 0),
        first_event=_as_utc(span[0]) if span[0] is not None else None,
        last_event=_as_utc(span[1]) if span[1] is not None else None,
    )


async def total_cost(
    session: AsyncSession, start: date, end: date
) -> Decimal:
    """Return the summed known cost over a day range (0 if none)."""
    statement = _apply_rollup_filters(
        select(func.sum(models.DailyRollup.cost_usd)), start, end, None, None, None, None
    )
    result = await session.execute(statement)
    value = result.scalar_one_or_none()
    return Decimal("0") if value is None else Decimal(str(value))


async def punch_card(
    session: AsyncSession,
    start: date,
    end: date,
    machine: str | None = None,
    project: str | None = None,
    roots: Sequence[str] = DEFAULT_ROOTS,
) -> dict[tuple[int, int], int]:
    """Return a (weekday, hour) -> total tokens map from usage_events.

    Weekday is 0=Monday..6=Sunday, hour is 0..23 (UTC). Bucketed in Python
    for dialect portability over the (bounded) requested range. ``project`` is
    matched against the normalized project group (so the punch card obeys the
    same filter as the rollup-backed charts).
    """
    event = models.UsageEvent
    start_ts = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_ts = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    statement = select(event.ts, total, event.project).where(
        event.ts >= start_ts, event.ts <= end_ts
    )
    if machine is not None:
        statement = statement.where(event.machine == machine)
    rows = (await session.execute(statement)).all()
    card: dict[tuple[int, int], int] = {}
    for ts, tokens, raw_project in rows:
        if project is not None and project_group(raw_project, roots) != project:
            continue
        aware = _as_utc(ts)
        key = (aware.weekday(), aware.hour)
        card[key] = card.get(key, 0) + int(tokens or 0)
    return card


@dataclass(frozen=True)
class PriceRowView:
    """A pricing table row for the API."""

    provider: str
    model: str
    effective_date: date
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_read_per_mtok: Decimal
    cache_write_short_per_mtok: Decimal
    cache_write_long_per_mtok: Decimal
    source: str


async def list_pricing(session: AsyncSession) -> list[PriceRowView]:
    """Return all pricing rows ordered by provider then model."""
    statement = select(models.Pricing).order_by(
        models.Pricing.provider, models.Pricing.model, models.Pricing.effective_date
    )
    result = await session.execute(statement)
    return [
        PriceRowView(
            provider=row.provider,
            model=row.model,
            effective_date=row.effective_date,
            input_per_mtok=row.input_per_mtok,
            output_per_mtok=row.output_per_mtok,
            cache_read_per_mtok=row.cache_read_per_mtok,
            cache_write_short_per_mtok=row.cache_write_short_per_mtok,
            cache_write_long_per_mtok=row.cache_write_long_per_mtok,
            source=row.source,
        )
        for row in result.scalars()
    ]


@dataclass(frozen=True)
class SessionSummary:
    """Aggregated stats for one session."""

    session_id: str
    provider: str
    machine: str | None
    project: str | None
    started_at: datetime
    last_at: datetime
    message_count: int
    total_tokens: int
    cost_usd: Decimal | None


async def list_sessions(
    session: AsyncSession,
    limit: int = 100,
    roots: Sequence[str] = DEFAULT_ROOTS,
) -> list[SessionSummary]:
    """Return recent sessions aggregated from usage_events, newest first.

    The raw ``cwd`` is folded to a project group so sessions display the same
    project labels as the rollup-backed breakdowns.
    """
    event = models.UsageEvent
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    statement = (
        select(
            event.session_id,
            func.min(event.provider),
            func.min(event.machine),
            func.min(event.project),
            func.min(event.ts),
            func.max(event.ts),
            func.count(),
            func.coalesce(func.sum(total), 0),
            func.sum(event.cost_usd),
        )
        .where(event.session_id.is_not(None))
        .group_by(event.session_id)
        .order_by(func.max(event.ts).desc())
        .limit(limit)
    )
    result = await session.execute(statement)
    summaries: list[SessionSummary] = []
    for row in result.all():
        summaries.append(
            SessionSummary(
                session_id=str(row[0]),
                provider=str(row[1]),
                machine=row[2],
                project=project_group(row[3], roots),
                started_at=_as_utc(row[4]),
                last_at=_as_utc(row[5]),
                message_count=int(row[6]),
                total_tokens=int(row[7] or 0),
                cost_usd=row[8] if row[8] is None else Decimal(str(row[8])),
            )
        )
    return summaries


def _as_utc(value: datetime) -> datetime:
    """Ensure a datetime read from the DB is timezone-aware (UTC)."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class SessionEvent:
    """One usage event within a session drill-down (metadata only)."""

    ts: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    total_tokens: int
    cost_usd: Decimal | None


@dataclass(frozen=True)
class SessionDetail:
    """A session's ordered event series and derived efficiency stats."""

    session_id: str
    project: str | None
    machine: str | None
    events: list[SessionEvent]
    stats: SessionStats


async def session_detail(
    session: AsyncSession, session_id: str, roots: Sequence[str] = DEFAULT_ROOTS
) -> SessionDetail | None:
    """Return one session's ordered events and stats, or None if unknown.

    No message text is read or returned; only token/cost metadata.
    """
    event = models.UsageEvent
    rows = list(
        (
            await session.execute(
                select(event).where(event.session_id == session_id).order_by(event.ts)
            )
        ).scalars()
    )
    if not rows:
        return None

    events: list[SessionEvent] = []
    for row in rows:
        total = (
            row.input_tokens
            + row.output_tokens
            + row.cache_read_tokens
            + row.cache_write_short_tokens
            + row.cache_write_long_tokens
        )
        events.append(
            SessionEvent(
                ts=_as_utc(row.ts),
                model=row.model,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cache_read_tokens=row.cache_read_tokens,
                cache_write_short_tokens=row.cache_write_short_tokens,
                cache_write_long_tokens=row.cache_write_long_tokens,
                total_tokens=total,
                cost_usd=row.cost_usd,
            )
        )
    stats = compute_session_stats(
        [e.total_tokens for e in events],
        [e.cache_read_tokens for e in events],
        [e.input_tokens for e in events],
    )
    return SessionDetail(
        session_id=session_id,
        project=project_group(rows[0].project, roots),
        machine=rows[0].machine,
        events=events,
        stats=stats,
    )


@dataclass(frozen=True)
class MachineSummary:
    """Fleet-view summary for one machine."""

    id: str
    platform: str | None
    last_seen: datetime | None
    collector_version: str | None
    total_tokens: int
    event_count: int


async def list_machines(session: AsyncSession) -> list[MachineSummary]:
    """Return every registered machine with its usage totals."""
    event = models.UsageEvent
    total = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    totals = (
        select(
            event.machine,
            func.coalesce(func.sum(total), 0).label("tokens"),
            func.count().label("events"),
        )
        .group_by(event.machine)
        .subquery()
    )
    statement = select(
        models.Machine,
        func.coalesce(totals.c.tokens, 0),
        func.coalesce(totals.c.events, 0),
    ).outerjoin(totals, totals.c.machine == models.Machine.id)
    result = await session.execute(statement)
    summaries: list[MachineSummary] = []
    for machine, tokens, events in result.all():
        summaries.append(
            MachineSummary(
                id=machine.id,
                platform=machine.platform,
                last_seen=_as_utc(machine.last_seen) if machine.last_seen else None,
                collector_version=machine.collector_version,
                total_tokens=int(tokens or 0),
                event_count=int(events or 0),
            )
        )
    return summaries
