"""Administrative deletion service: scoping, digest, cascade, rollup rebuild.

Service-level coverage for Task 70.3 against the migrated SQLite session.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.admin_deletion import (
    DeletionCriteria,
    DeletionDigestMismatchError,
    EmptyCriteriaError,
    execute_deletion,
    preview_deletion,
)
from tokemetry_server.services.rollups import refresh_rollups_for_days

_DAY = date(2026, 7, 10)
_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def _event(event_id: str, *, machine: str, project: str = "proj") -> models.UsageEventV2:
    return models.UsageEventV2(
        provider="anthropic",
        event_id=event_id,
        schema_version=2,
        event_kind="attempt",
        finality="final",
        sequence=0,
        native_model="claude-sonnet-4-5",
        ts_started=_TS,
        machine=machine,
        project=project,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_write_short_tokens=0,
        cache_write_long_tokens=0,
        reasoning_tokens=0,
        success=True,
        tool_call_count=0,
        provenance="official",
        dimensions={},
        extra={},
    )


async def _count(session: AsyncSession, model: type[object]) -> int:
    return int(
        await session.scalar(sa.select(sa.func.count()).select_from(model)) or 0
    )


async def _seed(session: AsyncSession) -> None:
    """Two machines' events for one day, with a cost and unit on box-a's e1."""
    session.add(_event("e1", machine="box-a"))
    session.add(_event("e2", machine="box-a"))
    session.add(_event("e3", machine="box-b"))
    await session.flush()
    session.add(
        models.ComputedCost(
            provider="anthropic",
            event_id="e1",
            pricing_version="v1",
            cost_status="priced",
            amount=Decimal("0.01"),
            calculated_at=_NOW,
        )
    )
    session.add(
        models.BillableUnit(
            provider="anthropic",
            event_id="e1",
            unit_type="web_search_request",
            quantity=Decimal("2"),
        )
    )
    session.add(
        models.UsageEventRevision(
            provider="anthropic",
            event_id="e1",
            sequence=0,
            finality="snapshot",
            payload={},
            reason="superseded",
            actor="ingest",
            ts=_TS,
        )
    )
    await session.commit()


async def test_preview_counts_match_by_machine(async_session: AsyncSession) -> None:
    await _seed(async_session)
    preview = await preview_deletion(async_session, DeletionCriteria(machine="box-a"))
    assert preview.counts["usage_events_v2"] == 2
    assert preview.counts["computed_costs"] == 1
    assert preview.counts["billable_units"] == 1
    assert preview.counts["usage_event_revisions"] == 1
    assert preview.affected_days == [_DAY]


async def test_empty_criteria_rejected(async_session: AsyncSession) -> None:
    with pytest.raises(EmptyCriteriaError):
        await preview_deletion(async_session, DeletionCriteria())


async def test_execute_deletes_cascade_and_leaves_others(
    async_session: AsyncSession,
) -> None:
    await _seed(async_session)
    preview = await preview_deletion(async_session, DeletionCriteria(machine="box-a"))
    result = await execute_deletion(
        async_session,
        DeletionCriteria(machine="box-a"),
        preview.digest,
        "admin",
        _NOW,
        "sqlite",
    )
    await async_session.commit()

    assert result.counts["usage_events_v2"] == 2
    # box-a events and all their dependents are gone; box-b survives.
    assert await _count(async_session, models.UsageEventV2) == 1
    assert await _count(async_session, models.ComputedCost) == 0
    assert await _count(async_session, models.BillableUnit) == 0
    assert await _count(async_session, models.UsageEventRevision) == 0
    survivor = await async_session.scalar(sa.select(models.UsageEventV2.machine))
    assert survivor == "box-b"


async def test_digest_mismatch_rejected(async_session: AsyncSession) -> None:
    await _seed(async_session)
    with pytest.raises(DeletionDigestMismatchError):
        await execute_deletion(
            async_session,
            DeletionCriteria(machine="box-a"),
            "stale-digest",
            "admin",
            _NOW,
            "sqlite",
        )


async def test_rollups_recomputed_after_deletion(
    async_session: AsyncSession,
) -> None:
    """Deleting a machine's events rebuilds the day's rollups without it."""
    await _seed(async_session)
    await refresh_rollups_for_days(async_session, "sqlite", [_DAY])
    await async_session.commit()
    # Both machines have a rollup row for the day.
    assert await _count(async_session, models.DailyRollup) == 2

    preview = await preview_deletion(async_session, DeletionCriteria(machine="box-a"))
    result = await execute_deletion(
        async_session,
        DeletionCriteria(machine="box-a"),
        preview.digest,
        "admin",
        _NOW,
        "sqlite",
    )
    await async_session.commit()

    assert result.rollups_recomputed == 1  # only box-b remains
    machines = {
        r.machine
        for r in (
            await async_session.execute(sa.select(models.DailyRollup))
        ).scalars()
    }
    assert machines == {"box-b"}


async def test_execute_writes_audit_entry(async_session: AsyncSession) -> None:
    await _seed(async_session)
    preview = await preview_deletion(async_session, DeletionCriteria(machine="box-a"))
    await execute_deletion(
        async_session,
        DeletionCriteria(machine="box-a"),
        preview.digest,
        "privacy-owner",
        _NOW,
        "sqlite",
    )
    await async_session.commit()
    row = (
        await async_session.execute(
            sa.select(models.AuditLog).where(
                models.AuditLog.action == "admin_data_delete"
            )
        )
    ).scalar_one()
    assert row.actor == "privacy-owner"
    assert row.detail["counts"]["usage_events_v2"] == 2
    assert row.detail["criteria"]["machine"] == "box-a"
    assert row.detail["digest"] == preview.digest


async def test_time_range_scoping(async_session: AsyncSession) -> None:
    """A time-range criterion deletes only events inside the window."""
    async_session.add(_event("old", machine="box-a"))
    newer = _event("new", machine="box-a")
    newer.ts_started = _TS + timedelta(days=2)
    async_session.add(newer)
    await async_session.commit()

    criteria = DeletionCriteria(
        start=_TS - timedelta(hours=1), end=_TS + timedelta(hours=1)
    )
    preview = await preview_deletion(async_session, criteria)
    await execute_deletion(
        async_session, criteria, preview.digest, "admin", _NOW, "sqlite"
    )
    await async_session.commit()
    remaining = await async_session.scalar(sa.select(models.UsageEventV2.event_id))
    assert remaining == "new"
