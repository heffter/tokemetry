"""Source registry: auto-register reporting sources from v2 payloads.

A source is a gateway, collector, SDK, importer, or manual actor -- distinct
from a machine (FR-SOURCE-003), so one machine may host several
(FR-SOURCE-009). Every v2 ingest resolves each event's ``source`` object to a
``sources`` row, creating it on first sight (D-011) and advancing ``last_seen``
and ``version`` thereafter. Identity is ``(type, name, instance_id)``; a
``NULL`` instance id is matched explicitly so a source without one is not
duplicated. Labels are mutable without changing event identity (FR-SOURCE-010),
and revoking a source never deletes history (FR-SOURCE-012).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef

from tokemetry_server.db import models
from tokemetry_server.services.data_quality import DataQualityService

#: Default per-source-type staleness thresholds in seconds (FR-SOURCE-005/006).
DEFAULT_STALE_THRESHOLDS: dict[str, float] = {
    "collector": 1800.0,
    "gateway": 600.0,
}
#: Fallback staleness threshold for source types without a specific one.
DEFAULT_STALE_SECONDS = 1800.0


class SourceRegistryService:
    """Resolves ``SourceRef`` payloads to ``sources`` rows, creating on first sight."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the service bound to the caller's transaction."""
        self._session = session

    async def resolve_or_create(
        self,
        source: SourceRef,
        ts: datetime,
        machine: str | None = None,
        token_label: str | None = None,
    ) -> int:
        """Return the id of the source, creating or refreshing its row.

        Idempotent on ``(type, name, instance_id)``: a first sighting inserts a
        row (``billing_mode='api_billed'`` by default, D-007); a repeat advances
        ``last_seen`` (never backwards), bumps ``version``, and fills a missing
        machine or token label without overwriting an existing one.
        """
        source_type = str(source.type)
        stmt = select(models.Source).where(
            models.Source.type == source_type,
            models.Source.name == source.name,
        )
        stmt = (
            stmt.where(models.Source.instance_id == source.instance_id)
            if source.instance_id is not None
            else stmt.where(models.Source.instance_id.is_(None))
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()

        if existing is None:
            row = models.Source(
                type=source_type,
                name=source.name,
                version=source.version,
                instance_id=source.instance_id,
                machine=machine,
                token_label=token_label,
                billing_mode="api_billed",
                first_seen=ts,
                last_seen=ts,
                revoked=False,
            )
            self._session.add(row)
            await self._session.flush()
            return row.id

        if ts > _as_naive(existing.last_seen, ts):
            existing.last_seen = ts
        existing.version = source.version
        if machine is not None and existing.machine is None:
            existing.machine = machine
        if token_label is not None and existing.token_label is None:
            existing.token_label = token_label
        return existing.id


def _as_naive(stored: datetime, incoming: datetime) -> datetime:
    """Align a stored timestamp's tz-awareness with ``incoming`` for comparison.

    SQLite reads timestamps back naive while ``incoming`` is tz-aware; compare on
    the same footing so ``last_seen`` advances correctly on both engines.
    """
    if stored.tzinfo is None and incoming.tzinfo is not None:
        return stored.replace(tzinfo=incoming.tzinfo)
    if stored.tzinfo is not None and incoming.tzinfo is None:
        return stored.replace(tzinfo=None)
    return stored


@dataclass(frozen=True)
class SourceHealth:
    """The query-time health of one source (FR-SOURCE-005/006)."""

    source_id: int
    last_successful_ingest: datetime | None
    recent_error_count: int
    reported_schema_version: int | None
    clock_skew_seconds: float | None
    stale: bool
    staleness_threshold_seconds: float


class SourceHealthService:
    """Records per-batch source health and evaluates staleness at query time.

    Health is derived from stored fields (no background job): each batch updates
    ``last_successful_ingest``, the rolling ``recent_error_count``, the reported
    schema version, and the clock skew (max event timestamp minus batch receipt
    time). A skew beyond the warning threshold is recorded as a ``clock_skew``
    data-quality event.
    """

    def __init__(
        self,
        session: AsyncSession,
        data_quality: DataQualityService | None = None,
        error_window_seconds: float = 3600.0,
        clock_skew_warn_seconds: float = 300.0,
        stale_thresholds: dict[str, float] | None = None,
        default_stale_seconds: float = DEFAULT_STALE_SECONDS,
    ) -> None:
        """Create the service with the health thresholds."""
        self._session = session
        self._dq = data_quality
        self._error_window = error_window_seconds
        self._clock_skew_warn = clock_skew_warn_seconds
        self._stale_thresholds = stale_thresholds or dict(DEFAULT_STALE_THRESHOLDS)
        self._default_stale = default_stale_seconds

    async def record_ingest(
        self,
        source_id: int,
        received_at: datetime,
        schema_version: int,
        max_event_ts: datetime | None,
        error_count: int,
    ) -> None:
        """Update a source's health from one processed batch."""
        source = await self._session.get(models.Source, source_id)
        if source is None:
            return

        source.reported_schema_version = schema_version
        if error_count == 0:
            source.last_successful_ingest = received_at

        window_start = source.error_window_started_at
        aligned_start = _as_naive(window_start, received_at) if window_start else None
        window_lapsed = (
            aligned_start is None
            or (received_at - aligned_start).total_seconds() > self._error_window
        )
        if window_lapsed:
            source.recent_error_count = error_count
            source.error_window_started_at = received_at
        else:
            source.recent_error_count = (source.recent_error_count or 0) + error_count

        if max_event_ts is not None:
            skew = (_as_naive(max_event_ts, received_at) - received_at).total_seconds()
            source.clock_skew_seconds = skew
            if abs(skew) > self._clock_skew_warn and self._dq is not None:
                await self._dq.record_safe(
                    "clock_skew",
                    f"source:{source_id}",
                    received_at,
                    detail={"skew_seconds": round(skew, 3)},
                )

    def staleness_threshold(self, source_type: str) -> float:
        """The staleness threshold (seconds) for a source type."""
        return self._stale_thresholds.get(source_type, self._default_stale)

    def health(self, source: models.Source, now: datetime | None = None) -> SourceHealth:
        """Compute a source's health at ``now`` (defaults to the current time)."""
        moment = now if now is not None else datetime.now(UTC)
        threshold = self.staleness_threshold(source.type)
        last = source.last_successful_ingest
        stale = (
            last is None
            or (moment - _as_naive(last, moment)).total_seconds() > threshold
        )
        return SourceHealth(
            source_id=source.id,
            last_successful_ingest=last,
            recent_error_count=source.recent_error_count or 0,
            reported_schema_version=source.reported_schema_version,
            clock_skew_seconds=source.clock_skew_seconds,
            stale=stale,
            staleness_threshold_seconds=threshold,
        )
