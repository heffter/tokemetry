"""Integration and unit tests for source health tracking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType
from tokemetry_server.db import models
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.sources import (
    SourceHealthService,
    SourceRegistryService,
)

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _source(source_type: SourceType = SourceType.GATEWAY) -> SourceRef:
    return SourceRef(type=source_type, name="proxy", version="1.0", instance_id="i1")


async def _make_source(session: AsyncSession, source_type: SourceType) -> int:
    source_id = await SourceRegistryService(session).resolve_or_create(
        _source(source_type), _TS
    )
    await session.commit()
    return source_id


async def test_healthy_ingest_sets_last_successful(async_session: AsyncSession) -> None:
    source_id = await _make_source(async_session, SourceType.GATEWAY)
    health = SourceHealthService(async_session)
    await health.record_ingest(source_id, _TS, schema_version=2, max_event_ts=_TS, error_count=0)
    await async_session.commit()

    source = await async_session.get(models.Source, source_id)
    assert source is not None
    assert source.last_successful_ingest is not None
    assert source.reported_schema_version == 2
    assert source.recent_error_count == 0
    assert health.health(source, now=_TS).stale is False


async def test_error_window_accumulates(async_session: AsyncSession) -> None:
    source_id = await _make_source(async_session, SourceType.GATEWAY)
    health = SourceHealthService(async_session, error_window_seconds=3600)
    await health.record_ingest(source_id, _TS, 2, _TS, error_count=2)
    await health.record_ingest(
        source_id, _TS + timedelta(minutes=10), 2, _TS, error_count=3
    )
    await async_session.commit()
    source = await async_session.get(models.Source, source_id)
    assert source is not None
    assert source.recent_error_count == 5  # within the window


async def test_error_window_resets_after_lapse(async_session: AsyncSession) -> None:
    source_id = await _make_source(async_session, SourceType.GATEWAY)
    health = SourceHealthService(async_session, error_window_seconds=3600)
    await health.record_ingest(source_id, _TS, 2, _TS, error_count=4)
    await health.record_ingest(
        source_id, _TS + timedelta(hours=2), 2, _TS, error_count=1
    )
    await async_session.commit()
    source = await async_session.get(models.Source, source_id)
    assert source is not None
    assert source.recent_error_count == 1  # window lapsed, reset


async def test_clock_skew_records_data_quality(async_session: AsyncSession) -> None:
    source_id = await _make_source(async_session, SourceType.GATEWAY)
    dq = DataQualityService(async_session)
    health = SourceHealthService(async_session, dq, clock_skew_warn_seconds=300)
    # Event timestamp 10 minutes ahead of receipt: positive (future) skew.
    future_event = _TS + timedelta(minutes=10)
    await health.record_ingest(source_id, _TS, 2, future_event, error_count=0)
    await async_session.commit()

    source = await async_session.get(models.Source, source_id)
    assert source is not None
    assert source.clock_skew_seconds == 600.0
    assert len(await dq.open_events("clock_skew")) == 1


async def test_negative_skew_within_threshold_no_dq(async_session: AsyncSession) -> None:
    source_id = await _make_source(async_session, SourceType.GATEWAY)
    dq = DataQualityService(async_session)
    health = SourceHealthService(async_session, dq, clock_skew_warn_seconds=300)
    # Event 1 minute in the past: small negative skew, under threshold.
    past_event = _TS - timedelta(minutes=1)
    await health.record_ingest(source_id, _TS, 2, past_event, error_count=0)
    await async_session.commit()

    source = await async_session.get(models.Source, source_id)
    assert source is not None
    assert source.clock_skew_seconds == -60.0
    assert await dq.open_events("clock_skew") == []


async def test_staleness_threshold_per_type(async_session: AsyncSession) -> None:
    collector_id = await _make_source(async_session, SourceType.COLLECTOR)
    health = SourceHealthService(async_session)
    assert health.staleness_threshold("collector") == 1800.0
    assert health.staleness_threshold("gateway") == 600.0
    assert health.staleness_threshold("sdk") == 1800.0  # default

    await health.record_ingest(collector_id, _TS, 2, _TS, error_count=0)
    await async_session.commit()
    source = await async_session.get(models.Source, collector_id)
    assert source is not None
    # 20 minutes later: a collector (30-min threshold) is still fresh...
    assert health.health(source, now=_TS + timedelta(minutes=20)).stale is False
    # ...but 40 minutes later it is stale.
    assert health.health(source, now=_TS + timedelta(minutes=40)).stale is True


async def test_never_ingested_source_is_stale(async_session: AsyncSession) -> None:
    source_id = await _make_source(async_session, SourceType.GATEWAY)
    health = SourceHealthService(async_session)
    source = await async_session.get(models.Source, source_id)
    assert source is not None
    assert health.health(source, now=_TS).stale is True  # never ingested
