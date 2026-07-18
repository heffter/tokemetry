"""Tests for the data-quality recording service (TOK-2, subtask 61.4).

Dedup-window collapsing, kind validation, resolution flagging, the
fire-and-forget guarantee, and an ingest that records an unknown model without
rolling back the accepted event (NFR-REL-008).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.data_quality import (
    DATA_QUALITY_KINDS,
    DataQualityService,
)

_TS = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)

_MACHINE = {"name": "box-1", "platform": "windows", "collector_version": "0.1.0"}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _row_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(models.DataQualityEvent)
    ) or 0


def _event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": "req_1",
        "provider": "anthropic",
        "native_model": "claude-brand-new-9",
        "ts": "2026-07-09T09:41:14+00:00",
        "session_id": "sess-1",
        "project": "C:\\devel\\tokemetry",
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_read_tokens": 500,
        "cache_write_short_tokens": 0,
        "cache_write_long_tokens": 200,
    }
    event.update(overrides)
    return event


class TestRecording:
    async def test_record_inserts_open_row(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        row = await service.record(
            "unknown_model", "anthropic/claude-x", _TS, detail={"native_model": "claude-x"}
        )
        await async_session.commit()
        assert row.resolved is False
        assert row.detail == {"native_model": "claude-x"}
        assert await _row_count(async_session) == 1

    async def test_unknown_kind_rejected(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        with pytest.raises(ValueError, match="unknown data-quality kind"):
            await service.record("bogus_kind", "subject", _TS)

    async def test_all_kinds_recordable(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        for index, kind in enumerate(DATA_QUALITY_KINDS):
            await service.record(kind, f"subject-{index}", _TS)
        await async_session.commit()
        assert await _row_count(async_session) == len(DATA_QUALITY_KINDS)


class TestDedupWindow:
    async def test_bursts_collapse_within_window(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session, dedup_window_seconds=3600)
        await service.record("unknown_model", "anthropic/m", _TS)
        await service.record("unknown_model", "anthropic/m", _TS + timedelta(seconds=30))
        await async_session.commit()
        assert await _row_count(async_session) == 1
        row = (await async_session.execute(select(models.DataQualityEvent))).scalar_one()
        assert _utc(row.ts) == _TS + timedelta(seconds=30)

    async def test_beyond_window_opens_new_record(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session, dedup_window_seconds=1)
        await service.record("unknown_model", "anthropic/m", _TS)
        await service.record("unknown_model", "anthropic/m", _TS + timedelta(hours=1))
        await async_session.commit()
        assert await _row_count(async_session) == 2

    async def test_distinct_subjects_do_not_collapse(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        await service.record("unknown_model", "anthropic/a", _TS)
        await service.record("unknown_model", "anthropic/b", _TS)
        await async_session.commit()
        assert await _row_count(async_session) == 2


class TestResolution:
    async def test_resolve_open_flags_and_reopens(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        await service.record("unknown_model", "anthropic/m", _TS)
        await async_session.commit()

        resolved = await service.resolve_open("unknown_model", "anthropic/m")
        await async_session.commit()
        assert resolved == 1
        assert await service.open_events() == []

        # A recurrence after resolution opens a fresh record, not a collapse.
        await service.record("unknown_model", "anthropic/m", _TS + timedelta(seconds=5))
        await async_session.commit()
        assert await _row_count(async_session) == 2
        assert len(await service.open_events()) == 1

    async def test_open_events_filter_by_kind(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        await service.record("unknown_model", "anthropic/m", _TS)
        await service.record("clock_skew", "box-1", _TS)
        await async_session.commit()
        models_only = await service.open_events(kind="unknown_model")
        assert len(models_only) == 1
        assert models_only[0].kind == "unknown_model"


class TestFireAndForget:
    async def test_record_safe_swallows_errors(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        # An invalid kind raises inside record(); record_safe must swallow it.
        await service.record_safe("bogus_kind", "subject", _TS)
        await async_session.commit()
        assert await _row_count(async_session) == 0

    async def test_record_safe_persists_valid_record(self, async_session: AsyncSession) -> None:
        service = DataQualityService(async_session)
        await service.record_safe("unknown_provider", "mistral", _TS, detail={"raw": "Mistral"})
        await async_session.commit()
        rows = await service.open_events()
        assert len(rows) == 1
        assert rows[0].kind == "unknown_provider"
        assert rows[0].subject == "mistral"


def test_ingest_records_unknown_model_without_rolling_back_event(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    """NFR-REL-008: in-transaction DQ recording keeps the accepted event."""
    response = client.post(
        "/api/v1/ingest/events",
        json={"machine": _MACHINE, "events": [_event()]},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": 1, "duplicates_merged": 0}

    with read_engine.connect() as conn:
        events = conn.execute(sa.text("SELECT COUNT(*) FROM usage_events")).scalar_one()
        dq = conn.execute(
            sa.text(
                "SELECT kind, subject FROM data_quality_events "
                "WHERE kind = 'unknown_model'"
            )
        ).one()
    assert events == 1
    assert dq.kind == "unknown_model"
    assert dq.subject == "anthropic/claude-brand-new-9"
