"""Limit-snapshot flood control, source resolution, and no-merge (Task 69.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import Provenance
from tokemetry_core.usage_v2 import LimitSnapshotV2, SourceRef, SourceType
from tokemetry_server.db import models
from tokemetry_server.services.ingest_v2_meta import MetaIngestV2Service

_T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _snapshot(
    ts: datetime,
    *,
    source_name: str,
    source_type: SourceType = SourceType.GATEWAY,
    window_kind: str = "requests_per_minute",
    provider: str = "anthropic",
) -> LimitSnapshotV2:
    return LimitSnapshotV2(
        schema_version=2,
        provider=provider,
        window_kind=window_kind,
        ts=ts,
        utilization_pct=50.0,
        limit_amount=1000.0,
        remaining=500.0,
        unit="requests",
        provenance=Provenance.LOCAL_ESTIMATE,
        source=SourceRef(type=source_type, name=source_name, version="1.0"),
    )


async def _rows(session: AsyncSession) -> list[models.LimitSnapshot]:
    return list((await session.execute(sa.select(models.LimitSnapshot))).scalars())


async def test_source_reference_resolves_to_source_id_and_dimensions(
    async_session: AsyncSession,
) -> None:
    service = MetaIngestV2Service(async_session, "sqlite")
    _, accepted = await service.ingest_limits([_snapshot(_T0, source_name="gw-1")])
    await async_session.commit()
    assert accepted == 1
    (row,) = await _rows(async_session)
    assert row.source_id is not None  # resolved, not left null
    assert row.provenance == "local_estimate"  # estimated provenance preserved
    assert float(row.limit_amount) == 1000.0
    assert row.unit == "requests"


async def test_flood_control_drops_rapid_repeats(async_session: AsyncSession) -> None:
    service = MetaIngestV2Service(async_session, "sqlite")
    snapshots = [
        _snapshot(_T0, source_name="gw-1"),
        _snapshot(_T0 + timedelta(seconds=10), source_name="gw-1"),  # too soon
        _snapshot(_T0 + timedelta(seconds=90), source_name="gw-1"),  # ok
    ]
    _, accepted = await service.ingest_limits(snapshots, min_interval_seconds=60)
    await async_session.commit()
    # Only the first and the third (>60s later) survive.
    assert accepted == 2
    assert len(await _rows(async_session)) == 2


async def test_flood_control_off_by_default_keeps_all(
    async_session: AsyncSession,
) -> None:
    service = MetaIngestV2Service(async_session, "sqlite")
    snapshots = [
        _snapshot(_T0, source_name="gw-1"),
        _snapshot(_T0 + timedelta(seconds=1), source_name="gw-1"),
    ]
    _, accepted = await service.ingest_limits(snapshots)  # interval defaults to 0
    await async_session.commit()
    assert accepted == 2


async def test_gateway_and_collector_streams_do_not_merge(
    async_session: AsyncSession,
) -> None:
    # FR-LIMIT-005: same provider + window from two sources are two streams, so
    # flood control on the gateway never suppresses the collector's snapshot.
    service = MetaIngestV2Service(async_session, "sqlite")
    snapshots = [
        _snapshot(_T0, source_name="gateway-1", source_type=SourceType.GATEWAY),
        _snapshot(
            _T0 + timedelta(seconds=5),
            source_name="collector-1",
            source_type=SourceType.COLLECTOR,
        ),
    ]
    _, accepted = await service.ingest_limits(snapshots, min_interval_seconds=60)
    await async_session.commit()
    rows = await _rows(async_session)
    assert accepted == 2
    assert len({row.source_id for row in rows}) == 2  # distinct sources
