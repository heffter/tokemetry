"""v2 limits, data-quality, and rollup read queries (Task 66.6)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.resource_queries import (
    list_data_quality,
    list_limits,
    list_rollups,
)

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 1, tzinfo=UTC)
_END = datetime(2026, 8, 1, tzinfo=UTC)


def _limit(session: AsyncSession, **fields: Any) -> None:
    defaults: dict[str, Any] = {
        "provider": "anthropic", "machine": "m1", "ts": _TS,
        "window_kind": "five_hour", "utilization_pct": Decimal("42.5"),
        "provenance": "official", "raw": {},
    }
    defaults.update(fields)
    session.add(models.LimitSnapshot(**defaults))


def _dq(session: AsyncSession, **fields: Any) -> None:
    defaults: dict[str, Any] = {
        "kind": "unknown_model", "subject": "anthropic/x", "detail": {},
        "ts": _TS, "resolved": False,
    }
    defaults.update(fields)
    session.add(models.DataQualityEvent(**defaults))


def _rollup(session: AsyncSession, **fields: Any) -> None:
    defaults: dict[str, Any] = {
        "day": date(2026, 7, 10), "provider": "anthropic", "model": "claude-sonnet-4-5",
        "machine": "", "project": "", "source": "", "environment": "",
        "billing_mode": "api_billed", "provenance": "derived",
        "input_tokens": 10, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_short_tokens": 0, "cache_write_long_tokens": 0,
        "reasoning_tokens": 0, "total_tokens": 10, "unpriced_event_count": 0,
    }
    defaults.update(fields)
    session.add(models.DailyRollup(**defaults))


async def test_list_limits_keyset_and_filters(async_session: AsyncSession) -> None:
    for i in range(3):
        _limit(async_session, ts=_TS + timedelta(hours=i))
    _limit(async_session, provider="openai", ts=_TS)
    await async_session.commit()

    page1 = await list_limits(
        async_session, _START, _END, "anthropic", None, None, None, None, 2
    )
    assert len(page1.items) == 2 and page1.next_cursor is not None
    assert all(r.provider == "anthropic" for r in page1.items)
    page2 = await list_limits(
        async_session, _START, _END, "anthropic", None, None, None, page1.next_cursor, 2
    )
    assert len(page2.items) == 1 and page2.next_cursor is None


async def test_list_data_quality_filters(async_session: AsyncSession) -> None:
    _dq(async_session, kind="unknown_model", resolved=False)
    _dq(async_session, kind="clock_skew", resolved=True)
    await async_session.commit()

    unknown = await list_data_quality(
        async_session, "unknown_model", None, None, None, None, 50
    )
    assert [e.kind for e in unknown.items] == ["unknown_model"]
    unresolved = await list_data_quality(async_session, None, None, None, False, None, 50)
    assert all(not e.resolved for e in unresolved.items)


async def test_list_rollups_keyset_and_filters(async_session: AsyncSession) -> None:
    _rollup(async_session, day=date(2026, 7, 10))
    _rollup(async_session, day=date(2026, 7, 11))
    _rollup(async_session, provider="openai", day=date(2026, 7, 10))
    await async_session.commit()

    anthropic = await list_rollups(
        async_session, date(2026, 7, 1), date(2026, 7, 31),
        "anthropic", None, None, None, None, None, None, 50,
    )
    assert len(anthropic.items) == 2
    assert all(r.provider == "anthropic" for r in anthropic.items)

    page1 = await list_rollups(
        async_session, date(2026, 7, 1), date(2026, 7, 31),
        None, None, None, None, None, None, None, 2,
    )
    assert len(page1.items) == 2 and page1.next_cursor is not None
