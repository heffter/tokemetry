"""Retention worker: bounded, resumable, verified-before-delete deletion.

Enforces the retention policy (Task 70.1) as a background sweep (Task 70.2). For
each deletion-active category it removes rows past their retention in a bounded,
oldest-first batch, so an interrupted sweep simply resumes on the next tick
(deleted rows never reappear -- FR-RET-002). Raw attempt events are deleted a
whole day at a time and only after the covering daily rollups are verified to
exist and match the day's event token sums (FR-RET-004, FR-ROLLUP-010); a
mismatch aborts that day and records a data-quality event. Referential
integrity is preserved by deleting dependents (computed_costs, billable_units)
before their events (FR-RET-003). Every category's outcome is persisted to
``retention_status`` for operational visibility (FR-RET-005).

The queries avoid dialect-specific date functions (day windows are computed in
Python), so behaviour is identical on SQLite and Postgres (FR-RET-007).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.db.base import Base
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.retention import (
    ALERT_EVENTS,
    AUDIT_RECORDS,
    CORRECTIONS,
    DAILY_ROLLUPS,
    INGEST_BATCHES,
    LIMIT_SNAPSHOTS,
    RAW_EVENTS,
    RETENTION_CATEGORIES,
    SUPERSEDED_SNAPSHOTS,
    V1_ARCHIVE,
    RetentionPolicy,
)

#: Data-quality kind recorded when a day's rollups do not cover its raw events.
ROLLUP_MISMATCH_KIND = "retention_rollup_mismatch"


@dataclass(frozen=True)
class CategoryResult:
    """Outcome of one category in one sweep."""

    category: str
    ran: bool
    deleted: int = 0
    pending_backlog: int = 0
    oldest_retained: datetime | None = None
    aborted_days: int = 0


@dataclass
class RetentionSweepResult:
    """Aggregate outcome of a full retention sweep."""

    categories: list[CategoryResult] = field(default_factory=list)

    @property
    def total_deleted(self) -> int:
        return sum(c.deleted for c in self.categories)

    @property
    def aborted_days(self) -> int:
        return sum(c.aborted_days for c in self.categories)

    def deleted(self, category: str) -> int:
        for c in self.categories:
            if c.category == category:
                return c.deleted
        return 0


# One simple time-based category: the ORM model, its timestamp column, the
# single-column primary key used for the bounded delete, and an optional extra
# WHERE clause. daily_rollups is keyed by a Date column and handled separately.
@dataclass(frozen=True)
class _SimpleSpec:
    # time_col/pk_col are SQLAlchemy mapped attributes (InstrumentedAttribute),
    # used dynamically to build the delete; typed Any so query building stays
    # dialect-agnostic without fighting the ORM descriptor types.
    model: type[Base]
    time_col: Any
    pk_col: Any
    extra: Any = None


def _simple_specs() -> dict[str, _SimpleSpec]:
    """Build the simple-category specs (call-time so columns bind lazily)."""
    rev = models.UsageEventRevision
    return {
        SUPERSEDED_SNAPSHOTS: _SimpleSpec(rev, rev.ts, rev.id, rev.reason != "correction"),
        CORRECTIONS: _SimpleSpec(rev, rev.ts, rev.id, rev.reason == "correction"),
        LIMIT_SNAPSHOTS: _SimpleSpec(
            models.LimitSnapshot, models.LimitSnapshot.ts, models.LimitSnapshot.id
        ),
        INGEST_BATCHES: _SimpleSpec(
            models.IngestBatch,
            models.IngestBatch.received_at,
            models.IngestBatch.batch_id,
        ),
        AUDIT_RECORDS: _SimpleSpec(
            models.AuditLog, models.AuditLog.ts, models.AuditLog.id
        ),
        ALERT_EVENTS: _SimpleSpec(
            models.AlertEvent, models.AlertEvent.ts, models.AlertEvent.id
        ),
    }


# Sum of every token tier -- the same measure the rollup service totals per day.
def _event_token_total() -> ColumnElement[int]:
    e = models.UsageEventV2
    return func.coalesce(
        func.sum(
            e.input_tokens
            + e.output_tokens
            + e.cache_read_tokens
            + e.cache_write_short_tokens
            + e.cache_write_long_tokens
            + e.reasoning_tokens
        ),
        0,
    )


async def _delete_simple(
    session: AsyncSession, spec: _SimpleSpec, cutoff: datetime, batch_size: int
) -> tuple[int, int, datetime | None]:
    """Delete one bounded oldest-first batch; return (deleted, backlog, oldest)."""
    where = spec.time_col < cutoff
    if spec.extra is not None:
        where = where & spec.extra
    keys = (
        await session.execute(
            select(spec.pk_col).where(where).order_by(spec.time_col).limit(batch_size)
        )
    ).scalars().all()
    deleted = 0
    if keys:
        await session.execute(delete(spec.model).where(spec.pk_col.in_(keys)))
        deleted = len(keys)

    backlog = (
        await session.scalar(select(func.count()).select_from(spec.model).where(where))
    ) or 0
    oldest_where: ColumnElement[bool] | None = spec.extra
    oldest_stmt = select(func.min(spec.time_col))
    if oldest_where is not None:
        oldest_stmt = oldest_stmt.where(oldest_where)
    oldest = await session.scalar(oldest_stmt)
    return deleted, backlog, _as_utc(oldest)


async def _delete_daily_rollups(
    session: AsyncSession, cutoff: datetime, batch_size: int
) -> tuple[int, int, datetime | None]:
    """Delete rollups for days entirely older than the cutoff (by Date)."""
    r = models.DailyRollup
    cutoff_day = cutoff.date()
    where = r.day < cutoff_day
    keys = (
        await session.execute(
            select(r.id).where(where).order_by(r.day).limit(batch_size)
        )
    ).scalars().all()
    deleted = 0
    if keys:
        await session.execute(delete(r).where(r.id.in_(keys)))
        deleted = len(keys)
    backlog = (
        await session.scalar(select(func.count()).select_from(r).where(where))
    ) or 0
    oldest_day = await session.scalar(select(func.min(r.day)))
    oldest = (
        datetime(oldest_day.year, oldest_day.month, oldest_day.day, tzinfo=UTC)
        if oldest_day is not None
        else None
    )
    return deleted, backlog, oldest


async def _delete_v1_archive(
    session: AsyncSession, cutoff: datetime
) -> tuple[int, int, datetime | None]:
    """Delete legacy-archive rows older than the cutoff.

    The archive is a frozen, non-growing legacy table (the pre-view
    ``usage_events``), so a single time-bounded delete is used rather than a
    per-tick batch. Skips silently if the table is absent (fresh installs that
    never had a v1 table).
    """
    if not await _table_exists(session, "usage_events_v1_archive"):
        return 0, 0, None
    count = await session.scalar(
        text("SELECT COUNT(*) FROM usage_events_v1_archive WHERE ts < :cutoff"),
        {"cutoff": cutoff},
    )
    await session.execute(
        text("DELETE FROM usage_events_v1_archive WHERE ts < :cutoff"),
        {"cutoff": cutoff},
    )
    backlog = 0  # everything eligible was just deleted
    oldest = await session.scalar(text("SELECT MIN(ts) FROM usage_events_v1_archive"))
    return int(count or 0), backlog, _as_utc(oldest)


async def _table_exists(session: AsyncSession, name: str) -> bool:
    """Whether a table exists, via the dialect's inspector."""
    conn = await session.connection()
    return await conn.run_sync(
        lambda sync_conn: sync_conn.dialect.has_table(sync_conn, name)
    )


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


async def _rollups_cover_day(session: AsyncSession, day: date) -> bool:
    """Whether daily rollups exist for ``day`` and match its event token sum."""
    start, end = _day_bounds(day)
    e = models.UsageEventV2
    event_total = await session.scalar(
        select(_event_token_total()).where(
            e.event_kind == "attempt",
            e.finality == "final",
            e.ts_started >= start,
            e.ts_started < end,
        )
    )
    r = models.DailyRollup
    rollup_count = await session.scalar(
        select(func.count()).select_from(r).where(r.day == day)
    )
    if not rollup_count:
        return False
    rollup_total = await session.scalar(
        select(func.coalesce(func.sum(r.total_tokens), 0)).where(r.day == day)
    )
    return int(event_total or 0) == int(rollup_total or 0)


async def _delete_day_events(session: AsyncSession, day: date) -> int:
    """Delete a day's events and their dependents (dependents first)."""
    start, end = _day_bounds(day)
    e = models.UsageEventV2
    in_day = select(e.provider, e.event_id).where(
        e.ts_started >= start, e.ts_started < end
    )
    count = (
        await session.scalar(
            select(func.count()).select_from(e).where(
                e.ts_started >= start, e.ts_started < end
            )
        )
    ) or 0
    if count == 0:
        return 0
    cc = models.ComputedCost
    bu = models.BillableUnit
    await session.execute(
        delete(cc).where(tuple_(cc.provider, cc.event_id).in_(in_day))
    )
    await session.execute(
        delete(bu).where(tuple_(bu.provider, bu.event_id).in_(in_day))
    )
    await session.execute(delete(e).where(e.ts_started >= start, e.ts_started < end))
    return count


async def _sweep_raw_events(
    session: AsyncSession,
    cutoff: datetime,
    now: datetime,
    batch_size: int,
    dq: DataQualityService,
) -> tuple[int, int, datetime | None, int]:
    """Delete whole days of raw events older than the cutoff, verified first.

    Returns (deleted, pending_backlog, oldest_retained, aborted_days).
    """
    e = models.UsageEventV2
    oldest_ts = await session.scalar(select(func.min(e.ts_started)))
    oldest_utc = _as_utc(oldest_ts)
    if oldest_utc is None:
        return 0, 0, None, 0
    cutoff_day = cutoff.date()

    deleted = 0
    aborted = 0
    day = oldest_utc.date()
    while day < cutoff_day and deleted < batch_size:
        if await _rollups_cover_day(session, day):
            deleted += await _delete_day_events(session, day)
        else:
            start, _ = _day_bounds(day)
            has_events = await session.scalar(
                select(func.count())
                .select_from(e)
                .where(e.ts_started >= start, e.ts_started < start + timedelta(days=1))
            )
            if has_events:
                aborted += 1
                await dq.record(
                    ROLLUP_MISMATCH_KIND,
                    f"{RAW_EVENTS}:{day.isoformat()}",
                    now,
                    detail={"day": day.isoformat()},
                )
        day += timedelta(days=1)

    start, _ = _day_bounds(cutoff_day)
    backlog = (
        await session.scalar(
            select(func.count()).select_from(e).where(e.ts_started < start)
        )
    ) or 0
    new_oldest = await session.scalar(select(func.min(e.ts_started)))
    return deleted, backlog, _as_utc(new_oldest), aborted


async def _record_status(
    session: AsyncSession,
    category: str,
    now: datetime,
    deleted: int,
    backlog: int,
    oldest: datetime | None,
) -> None:
    """Upsert the per-category retention status row."""
    row = await session.get(models.RetentionStatus, category)
    if row is None:
        row = models.RetentionStatus(category=category, total_deleted=0)
        session.add(row)
    row.last_run_at = now
    row.last_deleted = deleted
    row.total_deleted = (row.total_deleted or 0) + deleted
    row.pending_backlog = backlog
    row.oldest_retained = oldest
    row.updated_at = now


def _as_utc(value: datetime | None) -> datetime | None:
    """Coerce a possibly-naive stored timestamp to UTC (SQLite drops tzinfo)."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def run_retention_sweep(
    session: AsyncSession,
    policy: RetentionPolicy,
    now: datetime,
    *,
    batch_size: int = 5000,
    dedup_window_seconds: float = 3600.0,
) -> RetentionSweepResult:
    """Run one retention sweep across every category under ``policy``.

    Deletion-inactive categories (legal hold, disabled, or indefinite) are
    recorded as not-run and skipped. Callers commit the session.
    """
    result = RetentionSweepResult()
    dq = DataQualityService(session, dedup_window_seconds=dedup_window_seconds)
    specs = _simple_specs()

    for category in RETENTION_CATEGORIES:
        rule = policy.rules[category]
        if not policy.is_deletion_active(category):
            result.categories.append(CategoryResult(category=category, ran=False))
            continue
        assert rule.retention_days is not None  # deletion-active implies finite
        cutoff = now - timedelta(days=rule.retention_days)

        if category == RAW_EVENTS:
            deleted, backlog, oldest, aborted = await _sweep_raw_events(
                session, cutoff, now, batch_size, dq
            )
        elif category == DAILY_ROLLUPS:
            deleted, backlog, oldest = await _delete_daily_rollups(
                session, cutoff, batch_size
            )
            aborted = 0
        elif category == V1_ARCHIVE:
            deleted, backlog, oldest = await _delete_v1_archive(session, cutoff)
            aborted = 0
        else:
            deleted, backlog, oldest = await _delete_simple(
                session, specs[category], cutoff, batch_size
            )
            aborted = 0

        await _record_status(session, category, now, deleted, backlog, oldest)
        result.categories.append(
            CategoryResult(
                category=category,
                ran=True,
                deleted=deleted,
                pending_backlog=backlog,
                oldest_retained=oldest,
                aborted_days=aborted,
            )
        )

    return result
