"""Administrative, targeted deletion of usage data (Task 70.3).

The privacy-owner surface for GDPR-style erasure and mistake recovery
(FR-PRIV-007). Deletion is scoped by source, machine, project, and/or time
range, and runs as a two-step dry-run/confirm flow mirroring the pricing import:
:func:`preview_deletion` returns per-table counts and a content digest without
touching data; :func:`execute_deletion` recomputes the preview, requires the
caller's digest to still match, deletes dependents before events, optionally
recomputes the affected days' rollups, and writes an audit entry (FR-PRIV-009).

A legal hold blocks execution (FR-RET-006); the API layer enforces the
``admin:retention`` scope.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services import audit
from tokemetry_server.services.rollups import refresh_rollups_for_days

# Table keys reported in the per-table counts (stable order).
USAGE_EVENTS = "usage_events_v2"
COMPUTED_COSTS = "computed_costs"
BILLABLE_UNITS = "billable_units"
REVISIONS = "usage_event_revisions"
_COUNT_TABLES = (USAGE_EVENTS, COMPUTED_COSTS, BILLABLE_UNITS, REVISIONS)


class EmptyCriteriaError(ValueError):
    """A deletion request named no criteria (would match everything)."""


class DeletionDigestMismatchError(ValueError):
    """The confirm digest does not match the current data (stale dry run)."""


@dataclass(frozen=True)
class DeletionCriteria:
    """What to delete: any combination narrows the match (all are ANDed)."""

    source: str | None = None
    machine: str | None = None
    project: str | None = None
    start: datetime | None = None
    end: datetime | None = None

    def is_empty(self) -> bool:
        return not any(
            (self.source, self.machine, self.project, self.start, self.end)
        )

    def as_dict(self) -> dict[str, str | None]:
        """Content-free, JSON-serializable view for digest and audit."""
        return {
            "source": self.source,
            "machine": self.machine,
            "project": self.project,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
        }


@dataclass(frozen=True)
class DeletionPreview:
    """Dry-run result: per-table counts, affected days, and a content digest."""

    counts: dict[str, int]
    affected_days: list[date]
    digest: str


@dataclass(frozen=True)
class DeletionResult:
    """Confirmed deletion outcome."""

    counts: dict[str, int]
    affected_days: list[date]
    digest: str
    rollups_recomputed: int


def _conditions(criteria: DeletionCriteria) -> list[ColumnElement[bool]]:
    """Build the usage_events_v2 WHERE conditions for the criteria."""
    e = models.UsageEventV2
    conds: list[ColumnElement[bool]] = []
    if criteria.machine is not None:
        conds.append(e.machine == criteria.machine)
    if criteria.project is not None:
        conds.append(e.project == criteria.project)
    if criteria.source is not None:
        conds.append(
            e.source_id.in_(
                select(models.Source.id).where(models.Source.name == criteria.source)
            )
        )
    if criteria.start is not None:
        conds.append(e.ts_started >= criteria.start)
    if criteria.end is not None:
        conds.append(e.ts_started < criteria.end)
    return conds


def _matched_events(where: ColumnElement[bool]) -> Select[Any]:
    """A select of the matched (provider, event_id) pairs."""
    e = models.UsageEventV2
    return select(e.provider, e.event_id).where(where)


def _digest(criteria: DeletionCriteria, counts: dict[str, int]) -> str:
    """Deterministic sha256 over criteria + counts, for stale-confirm detection."""
    canonical = json.dumps(
        {"criteria": criteria.as_dict(), "counts": counts},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _affected_days(session: AsyncSession, where: ColumnElement[bool]) -> list[date]:
    """The inclusive day span of matched events (for rollup recomputation)."""
    e = models.UsageEventV2
    span = (
        await session.execute(
            select(func.min(e.ts_started), func.max(e.ts_started)).where(where)
        )
    ).one()
    lo, hi = span
    if lo is None or hi is None:
        return []
    lo = lo if lo.tzinfo else lo.replace(tzinfo=UTC)
    hi = hi if hi.tzinfo else hi.replace(tzinfo=UTC)
    days: list[date] = []
    day = lo.date()
    while day <= hi.date():
        days.append(day)
        day += timedelta(days=1)
    return days


async def preview_deletion(
    session: AsyncSession, criteria: DeletionCriteria
) -> DeletionPreview:
    """Count what the criteria would delete, per table, without deleting.

    Raises:
        EmptyCriteriaError: If no criteria were supplied.
    """
    if criteria.is_empty():
        raise EmptyCriteriaError("at least one deletion criterion is required")
    where = and_(*_conditions(criteria))
    matched = _matched_events(where)

    e = models.UsageEventV2
    cc = models.ComputedCost
    bu = models.BillableUnit
    rev = models.UsageEventRevision
    counts = {
        USAGE_EVENTS: int(
            await session.scalar(select(func.count()).select_from(e).where(where)) or 0
        ),
        COMPUTED_COSTS: int(
            await session.scalar(
                select(func.count())
                .select_from(cc)
                .where(tuple_(cc.provider, cc.event_id).in_(matched))
            )
            or 0
        ),
        BILLABLE_UNITS: int(
            await session.scalar(
                select(func.count())
                .select_from(bu)
                .where(tuple_(bu.provider, bu.event_id).in_(matched))
            )
            or 0
        ),
        REVISIONS: int(
            await session.scalar(
                select(func.count())
                .select_from(rev)
                .where(tuple_(rev.provider, rev.event_id).in_(matched))
            )
            or 0
        ),
    }
    days = await _affected_days(session, where)
    return DeletionPreview(counts=counts, affected_days=days, digest=_digest(criteria, counts))


async def execute_deletion(
    session: AsyncSession,
    criteria: DeletionCriteria,
    expected_digest: str,
    actor: str | None,
    now: datetime,
    dialect_name: str,
    *,
    recompute_rollups: bool = True,
) -> DeletionResult:
    """Delete after verifying the caller's digest; audited.

    Deletes dependents (computed_costs, billable_units, revisions) before the
    events, then -- when ``recompute_rollups`` -- drops and rebuilds the affected
    days' rollups so no stale grain lingers.

    Raises:
        EmptyCriteriaError: If no criteria were supplied.
        DeletionDigestMismatchError: If the data changed since the dry run.
    """
    preview = await preview_deletion(session, criteria)
    if preview.digest != expected_digest:
        raise DeletionDigestMismatchError(
            "deletion digest does not match the current data; re-run the dry run"
        )

    where = and_(*_conditions(criteria))
    matched = _matched_events(where)
    cc = models.ComputedCost
    bu = models.BillableUnit
    rev = models.UsageEventRevision
    e = models.UsageEventV2
    await session.execute(delete(cc).where(tuple_(cc.provider, cc.event_id).in_(matched)))
    await session.execute(delete(bu).where(tuple_(bu.provider, bu.event_id).in_(matched)))
    await session.execute(delete(rev).where(tuple_(rev.provider, rev.event_id).in_(matched)))
    await session.execute(delete(e).where(where))

    recomputed = 0
    if recompute_rollups and preview.affected_days:
        await session.execute(
            delete(models.DailyRollup).where(
                models.DailyRollup.day.in_(preview.affected_days)
            )
        )
        recomputed = await refresh_rollups_for_days(
            session, dialect_name, preview.affected_days
        )

    audit.record(
        session,
        actor=actor,
        action="admin_data_delete",
        subject=_subject(criteria),
        detail={
            "criteria": criteria.as_dict(),
            "digest": preview.digest,
            "counts": preview.counts,
            "affected_days": [d.isoformat() for d in preview.affected_days],
            "rollups_recomputed": recomputed,
        },
        ts=now,
    )
    return DeletionResult(
        counts=preview.counts,
        affected_days=preview.affected_days,
        digest=preview.digest,
        rollups_recomputed=recomputed,
    )


def _subject(criteria: DeletionCriteria) -> str:
    """A short human-readable audit subject from the set criteria."""
    parts = [
        f"{key}={value}"
        for key, value in criteria.as_dict().items()
        if value is not None
    ]
    return ", ".join(parts) if parts else "all"
