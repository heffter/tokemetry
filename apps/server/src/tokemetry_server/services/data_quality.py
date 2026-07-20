"""Data-quality event recording and querying (provider-neutral v2, TOK-2).

A fire-and-forget sink for pipeline anomalies -- unknown providers/models,
schema drift, sequence conflicts, unpriced usage, limit-source failures, clock
skew. Recording must never fail otherwise-valid ingest, so callers use
:meth:`DataQualityService.record_safe`, which isolates the write in a SAVEPOINT
and swallows errors (NFR-REL-008).

Bursts are collapsed: within a configurable window there is at most one open
(unresolved) record per ``(kind, subject)``, so a recurring anomaly is a single
row whose timestamp advances rather than one row per event. This is the sink
used by FR-MODEL-006, FR-IDEMP-008, FR-PRICE-022, FR-LIMIT-013, and Epic TOK-9.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models

#: Recognized data-quality event kinds (extended as new detectors land).
DATA_QUALITY_KINDS = (
    "unknown_provider",
    "unknown_model",
    "schema_drift",
    "sequence_conflict",
    "unpriced_usage",
    "limit_source_failure",
    "clock_skew",
    "retention_rollup_mismatch",
)


def _as_utc(value: datetime) -> datetime:
    """Coerce a possibly-naive stored timestamp to UTC (SQLite drops tzinfo)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class DataQualityService:
    """Record and query data-quality anomalies against ``data_quality_events``."""

    def __init__(self, session: AsyncSession, dedup_window_seconds: float = 3600.0) -> None:
        """Create the service.

        Args:
            session: Active async session; the caller owns the transaction.
            dedup_window_seconds: Bursts of the same ``(kind, subject)`` within
                this window collapse onto one open record.
        """
        self._session = session
        self._window = timedelta(seconds=dedup_window_seconds)

    async def record(
        self,
        kind: str,
        subject: str,
        ts: datetime,
        detail: dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> models.DataQualityEvent:
        """Record an anomaly, collapsing onto an open record within the window.

        Raises:
            ValueError: If ``kind`` is not a recognized data-quality kind.
        """
        if kind not in DATA_QUALITY_KINDS:
            raise ValueError(f"unknown data-quality kind: {kind!r}")

        existing = await self._open_record(kind, subject)
        if existing is not None and self._within_window(ts, existing.ts):
            existing.ts = ts
            if detail is not None:
                existing.detail = detail
            if source_id is not None:
                existing.source_id = source_id
            return existing

        row = models.DataQualityEvent(
            kind=kind,
            subject=subject,
            detail=detail if detail is not None else {},
            source_id=source_id,
            ts=ts,
            resolved=False,
        )
        self._session.add(row)
        return row

    async def record_safe(
        self,
        kind: str,
        subject: str,
        ts: datetime,
        detail: dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> None:
        """Record an anomaly without ever propagating an error to the caller.

        The write runs inside a SAVEPOINT so a failure rolls back only the
        recording, never the surrounding ingest transaction (NFR-REL-008).
        """
        # Fire-and-forget by design: any failure here (including a DB error
        # that poisoned the savepoint) must not propagate to the caller.
        try:
            async with self._session.begin_nested():
                await self.record(kind, subject, ts, detail=detail, source_id=source_id)
        except Exception as exc:
            logger.warning("data quality recording failed ({}/{}): {}", kind, subject, exc)

    async def resolve_open(self, kind: str, subject: str) -> int:
        """Mark every open record for ``(kind, subject)`` resolved; return count."""
        rows = (
            await self._session.execute(
                select(models.DataQualityEvent).where(
                    models.DataQualityEvent.kind == kind,
                    models.DataQualityEvent.subject == subject,
                    models.DataQualityEvent.resolved.is_(False),
                )
            )
        ).scalars().all()
        for row in rows:
            row.resolved = True
        return len(rows)

    async def open_events(self, kind: str | None = None) -> list[models.DataQualityEvent]:
        """Return open (unresolved) records, newest first, optionally by kind."""
        stmt = select(models.DataQualityEvent).where(
            models.DataQualityEvent.resolved.is_(False)
        )
        if kind is not None:
            stmt = stmt.where(models.DataQualityEvent.kind == kind)
        stmt = stmt.order_by(models.DataQualityEvent.ts.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def _open_record(self, kind: str, subject: str) -> models.DataQualityEvent | None:
        stmt = (
            select(models.DataQualityEvent)
            .where(
                models.DataQualityEvent.kind == kind,
                models.DataQualityEvent.subject == subject,
                models.DataQualityEvent.resolved.is_(False),
            )
            .order_by(models.DataQualityEvent.ts.desc())
        )
        return (await self._session.execute(stmt)).scalars().first()

    def _within_window(self, new_ts: datetime, existing_ts: datetime) -> bool:
        """Whether ``new_ts`` is within the dedup window of ``existing_ts``."""
        return (_as_utc(new_ts) - _as_utc(existing_ts)) <= self._window
