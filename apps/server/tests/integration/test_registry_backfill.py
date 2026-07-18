"""Tests for the historical registry backfill (TOK-2, subtask 61.6).

A seeded history with dated and undated Claude ids, an unknown model, and a
synthetic provider (via a limit snapshot) drives the backfill; the tests assert
registry rows, lifecycle assignment, timestamps, and data-quality records, plus
idempotency, the guard marker, and startup wiring.
"""

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.registry_backfill import (
    BACKFILL_MARKER_KEY,
    RegistryBackfill,
)

_T1 = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
_T2 = datetime(2026, 2, 1, 8, 0, 0, tzinfo=UTC)
_T3 = datetime(2026, 1, 15, 8, 0, 0, tzinfo=UTC)
_T4 = datetime(2026, 1, 20, 8, 0, 0, tzinfo=UTC)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _usage_event(event_id: str, model: str, ts: datetime, **overrides: Any) -> models.UsageEvent:
    row = models.UsageEvent(
        provider="anthropic",
        event_id=event_id,
        ts=ts,
        model=model,
        provenance="official",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


async def _seed_history(session: AsyncSession) -> None:
    session.add_all(
        [
            _usage_event("e1", "claude-3-5-sonnet-20241022", _T1),
            _usage_event("e2", "claude-3-5-sonnet-20241022", _T2),
            _usage_event("e3", "claude-fable-5", _T3),
            _usage_event("e4", "mystery-model-x", _T4),
        ]
    )
    session.add(
        models.LimitSnapshot(
            provider="acme",
            ts=_T1,
            window_kind="five_hour",
            utilization_pct=12.5,
            provenance="official",
            raw={},
        )
    )
    await session.commit()


async def _model_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(models.Model)) or 0


async def _dq_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(models.DataQualityEvent)
    ) or 0


class TestBackfill:
    async def test_populates_registries(self, async_session: AsyncSession) -> None:
        await _seed_history(async_session)
        result = await RegistryBackfill(async_session).run()
        await async_session.commit()

        assert result.skipped is False
        assert result.providers == 2  # anthropic (events) + acme (limits)
        assert result.models_active == 2  # both claude ids
        assert result.models_unknown == 1  # mystery-model-x

        dated = await async_session.get(
            models.Model, ("anthropic", "claude-3-5-sonnet-20241022")
        )
        assert dated is not None
        assert dated.lifecycle == "active"
        assert _utc(dated.first_seen) == _T1
        assert _utc(dated.last_seen) == _T2

        undated = await async_session.get(models.Model, ("anthropic", "claude-fable-5"))
        assert undated is not None and undated.lifecycle == "active"

        mystery = await async_session.get(models.Model, ("anthropic", "mystery-model-x"))
        assert mystery is not None and mystery.lifecycle == "unknown"

    async def test_providers_backfilled_with_registration(
        self, async_session: AsyncSession
    ) -> None:
        await _seed_history(async_session)
        await RegistryBackfill(async_session).run()
        await async_session.commit()

        anthropic = await async_session.get(models.Provider, "anthropic")
        assert anthropic is not None and anthropic.registered is True
        acme = await async_session.get(models.Provider, "acme")
        assert acme is not None and acme.registered is False

    async def test_unknown_model_records_data_quality(
        self, async_session: AsyncSession
    ) -> None:
        await _seed_history(async_session)
        await RegistryBackfill(async_session).run()
        await async_session.commit()

        rows = (
            await async_session.execute(
                select(models.DataQualityEvent).where(
                    models.DataQualityEvent.kind == "unknown_model"
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].subject == "anthropic/mystery-model-x"
        assert rows[0].detail["source"] == "backfill"

    async def test_never_mutates_usage_rows(self, async_session: AsyncSession) -> None:
        await _seed_history(async_session)
        before = await async_session.scalar(
            select(func.count()).select_from(models.UsageEvent)
        )
        await RegistryBackfill(async_session).run()
        await async_session.commit()
        after = await async_session.scalar(
            select(func.count()).select_from(models.UsageEvent)
        )
        assert before == after == 4


class TestIdempotencyAndGuard:
    async def test_second_run_is_skipped(self, async_session: AsyncSession) -> None:
        await _seed_history(async_session)
        await RegistryBackfill(async_session).run()
        await async_session.commit()
        models_after_first = await _model_count(async_session)

        second = await RegistryBackfill(async_session).run()
        await async_session.commit()
        assert second.skipped is True
        assert await _model_count(async_session) == models_after_first

    async def test_marker_is_set(self, async_session: AsyncSession) -> None:
        await _seed_history(async_session)
        await RegistryBackfill(async_session).run()
        await async_session.commit()
        marker = await async_session.get(models.AppSetting, BACKFILL_MARKER_KEY)
        assert marker is not None

    async def test_force_reruns_without_duplicating(
        self, async_session: AsyncSession
    ) -> None:
        await _seed_history(async_session)
        await RegistryBackfill(async_session).run()
        await async_session.commit()
        models_first = await _model_count(async_session)
        dq_first = await _dq_count(async_session)

        forced = await RegistryBackfill(async_session).run(force=True)
        await async_session.commit()
        assert forced.skipped is False
        assert await _model_count(async_session) == models_first
        assert await _dq_count(async_session) == dq_first  # dedup window collapses


def test_startup_runs_backfill_marker(
    client: TestClient, read_engine: sa.Engine
) -> None:
    """App startup runs the backfill, leaving the marker set on an empty DB."""
    with read_engine.connect() as conn:
        marker = conn.execute(
            sa.text("SELECT value FROM app_settings WHERE key = :k"),
            {"k": BACKFILL_MARKER_KEY},
        ).scalar_one_or_none()
    assert marker is not None
