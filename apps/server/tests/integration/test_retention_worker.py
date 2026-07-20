"""Retention worker: batch progression, resume, verify-before-delete, integrity.

Service-level coverage for Task 70.2 against the migrated SQLite session. The
cross-dialect delete queries are additionally exercised on Postgres by the
dual-engine acceptance runs when ``TOKEMETRY_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.db.base import Base
from tokemetry_server.services.retention import (
    ALERT_EVENTS,
    AUDIT_RECORDS,
    DEFAULT_RETENTION_POLICY,
    RAW_EVENTS,
    CategoryRule,
    RetentionPolicy,
)
from tokemetry_server.services.retention_worker import (
    ROLLUP_MISMATCH_KIND,
    run_retention_sweep,
)

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


def _policy(legal_hold: bool = False, **overrides: CategoryRule) -> RetentionPolicy:
    rules = dict(DEFAULT_RETENTION_POLICY.rules)
    rules.update(overrides)
    return RetentionPolicy(rules=rules, legal_hold=legal_hold)


def _event(
    event_id: str, ts: datetime, *, input_tokens: int = 100, output_tokens: int = 50
) -> models.UsageEventV2:
    return models.UsageEventV2(
        provider="anthropic",
        event_id=event_id,
        schema_version=2,
        event_kind="attempt",
        finality="final",
        sequence=0,
        native_model="claude-sonnet-4-5",
        ts_started=ts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
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


def _rollup(day: date, total_tokens: int) -> models.DailyRollup:
    return models.DailyRollup(
        day=day,
        provider="anthropic",
        model="claude-sonnet-4-5",
        total_tokens=total_tokens,
    )


async def _count(session: AsyncSession, model: type[Base]) -> int:
    return int(await session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


async def _seed_day(session: AsyncSession, day: date, ids: list[str]) -> None:
    """Seed events for a day plus a matching (verified) rollup."""
    ts = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
    for event_id in ids:
        session.add(_event(event_id, ts))
    # total_tokens must equal the day's event token sum for verification to pass.
    session.add(_rollup(day, 150 * len(ids)))


async def test_deletes_verified_raw_events_and_records_status(
    async_session: AsyncSession,
) -> None:
    """A day with a matching rollup is deleted; status reflects it."""
    await _seed_day(async_session, date(2026, 7, 10), ["e1", "e2"])
    await async_session.commit()

    result = await run_retention_sweep(
        async_session, _policy(raw_events=CategoryRule(2, True)), _NOW
    )
    await async_session.commit()

    assert result.deleted(RAW_EVENTS) == 2
    assert await _count(async_session, models.UsageEventV2) == 0
    # The rollup (indefinite) is retained as the summary.
    assert await _count(async_session, models.DailyRollup) == 1
    status = await async_session.get(models.RetentionStatus, RAW_EVENTS)
    assert status is not None
    assert status.last_deleted == 2
    assert status.total_deleted == 2


async def test_keeps_events_within_retention(async_session: AsyncSession) -> None:
    """Recent events (inside the window) are never deleted."""
    recent = _NOW - timedelta(days=1)
    async_session.add(_event("recent", recent))
    async_session.add(_rollup(recent.date(), 150))
    await async_session.commit()

    await run_retention_sweep(
        async_session, _policy(raw_events=CategoryRule(2, True)), _NOW
    )
    await async_session.commit()
    assert await _count(async_session, models.UsageEventV2) == 1


async def test_verification_failure_aborts_day_and_records_dq(
    async_session: AsyncSession,
) -> None:
    """A day whose rollup is missing is not deleted and raises a DQ event."""
    ts = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    async_session.add(_event("e1", ts))
    async_session.add(_event("e2", ts))
    # No rollup seeded -> coverage check fails.
    await async_session.commit()

    result = await run_retention_sweep(
        async_session, _policy(raw_events=CategoryRule(2, True)), _NOW
    )
    await async_session.commit()

    assert result.deleted(RAW_EVENTS) == 0
    assert result.aborted_days == 1
    assert await _count(async_session, models.UsageEventV2) == 2  # untouched
    dq = (
        await async_session.execute(
            sa.select(models.DataQualityEvent).where(
                models.DataQualityEvent.kind == ROLLUP_MISMATCH_KIND
            )
        )
    ).scalars().all()
    assert len(dq) == 1
    assert dq[0].subject == f"{RAW_EVENTS}:2026-07-10"


async def test_mismatched_rollup_total_aborts_day(
    async_session: AsyncSession,
) -> None:
    """A rollup that exists but does not match the event sum aborts the day."""
    day = date(2026, 7, 10)
    ts = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    async_session.add(_event("e1", ts))
    async_session.add(_rollup(day, 999))  # wrong total (event sum is 150)
    await async_session.commit()

    result = await run_retention_sweep(
        async_session, _policy(raw_events=CategoryRule(2, True)), _NOW
    )
    await async_session.commit()
    assert result.deleted(RAW_EVENTS) == 0
    assert await _count(async_session, models.UsageEventV2) == 1


async def test_legal_hold_suspends_all_deletion(
    async_session: AsyncSession,
) -> None:
    """Under a legal hold nothing is deleted, even verified days."""
    await _seed_day(async_session, date(2026, 7, 10), ["e1", "e2"])
    await async_session.commit()

    result = await run_retention_sweep(
        async_session,
        _policy(legal_hold=True, raw_events=CategoryRule(2, True)),
        _NOW,
    )
    await async_session.commit()
    assert result.total_deleted == 0
    assert await _count(async_session, models.UsageEventV2) == 2


async def test_deletes_dependents_before_events(
    async_session: AsyncSession,
) -> None:
    """Computed costs and billable units of deleted events go with them."""
    await _seed_day(async_session, date(2026, 7, 10), ["e1"])
    async_session.add(
        models.ComputedCost(
            provider="anthropic",
            event_id="e1",
            pricing_version="v1",
            cost_status="priced",
            amount=Decimal("0.01"),
            calculated_at=_NOW,
        )
    )
    async_session.add(
        models.BillableUnit(
            provider="anthropic",
            event_id="e1",
            unit_type="web_search_request",
            quantity=Decimal("2"),
        )
    )
    await async_session.commit()

    await run_retention_sweep(
        async_session, _policy(raw_events=CategoryRule(2, True)), _NOW
    )
    await async_session.commit()
    assert await _count(async_session, models.UsageEventV2) == 0
    assert await _count(async_session, models.ComputedCost) == 0
    assert await _count(async_session, models.BillableUnit) == 0


async def test_batch_bound_and_resume(async_session: AsyncSession) -> None:
    """A small batch deletes the oldest day first; a re-run drains the rest."""
    await _seed_day(async_session, date(2026, 7, 8), ["a1", "a2"])
    await _seed_day(async_session, date(2026, 7, 9), ["b1", "b2"])
    await async_session.commit()

    # batch_size 1 stops after the first (oldest) day's 2 events.
    first = await run_retention_sweep(
        async_session, _policy(raw_events=CategoryRule(2, True)), _NOW, batch_size=1
    )
    await async_session.commit()
    assert first.deleted(RAW_EVENTS) == 2
    remaining = {
        e.event_id
        for e in (
            await async_session.execute(sa.select(models.UsageEventV2))
        ).scalars()
    }
    assert remaining == {"b1", "b2"}  # newer day survives the first pass

    second = await run_retention_sweep(
        async_session, _policy(raw_events=CategoryRule(2, True)), _NOW, batch_size=1
    )
    await async_session.commit()
    assert second.deleted(RAW_EVENTS) == 2
    assert await _count(async_session, models.UsageEventV2) == 0


async def test_simple_categories_delete_old_rows(
    async_session: AsyncSession,
) -> None:
    """Audit and alert-event categories delete rows past their retention."""
    old = _NOW - timedelta(days=500)
    recent = _NOW - timedelta(days=1)
    async_session.add(
        models.AuditLog(action="x", subject="s", detail={}, ts=old)
    )
    async_session.add(
        models.AuditLog(action="y", subject="s", detail={}, ts=recent)
    )
    rule = models.AlertRule(name="r", kind="limit_pct", channels=[])
    async_session.add(rule)
    await async_session.flush()
    async_session.add(
        models.AlertEvent(rule_id=rule.id, ts=old, severity="info", title="t", body="b")
    )
    async_session.add(
        models.AlertEvent(
            rule_id=rule.id, ts=recent, severity="info", title="t", body="b"
        )
    )
    await async_session.commit()

    result = await run_retention_sweep(async_session, _policy(), _NOW)
    await async_session.commit()
    assert result.deleted(AUDIT_RECORDS) == 1
    assert result.deleted(ALERT_EVENTS) == 1
    assert await _count(async_session, models.AuditLog) == 1  # recent kept
    assert await _count(async_session, models.AlertEvent) == 1


async def test_indefinite_and_disabled_categories_are_skipped(
    async_session: AsyncSession,
) -> None:
    """daily_rollups (indefinite) and v1_archive (disabled) never delete."""
    old = _NOW - timedelta(days=500)
    async_session.add(_rollup(old.date(), 10))
    await async_session.commit()

    result = await run_retention_sweep(async_session, _policy(), _NOW)
    await async_session.commit()
    ran = {c.category for c in result.categories if c.ran}
    assert "daily_rollups" not in ran
    assert "v1_archive" not in ran
    assert await _count(async_session, models.DailyRollup) == 1
