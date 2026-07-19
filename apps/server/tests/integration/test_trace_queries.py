"""Trace query service: attempts, logical requests, sessions (Task 66.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.query_framework import QueryFilters
from tokemetry_server.services.trace_queries import (
    list_attempts,
    list_requests,
    list_sessions,
    request_detail,
    scoped_session_id,
    session_detail,
)

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 1, tzinfo=UTC)
_END = datetime(2026, 8, 1, tzinfo=UTC)
_NONE = QueryFilters()


def _attempt(
    session: AsyncSession, event_id: str, *, ts: datetime = _TS, sequence: int = 0,
    **fields: Any,
) -> models.UsageEventV2:
    event = make_v1_event(
        provider="anthropic", event_id=event_id, model="claude-sonnet-4-5", ts=ts, **fields
    )
    event.sequence = sequence
    session.add(event)
    return event


def _logical(
    session: AsyncSession, logical_request_id: str, **fields: Any
) -> None:
    defaults: dict[str, Any] = {
        "provider": "anthropic", "logical_request_id": logical_request_id,
        "requested_model": "claude-sonnet-4-5", "session_id": "s1",
        "attempt_count": 1, "fallback_count": 0, "ts_first": _TS, "ts_last": _TS,
    }
    defaults.update(fields)
    session.add(models.LogicalRequest(**defaults))


async def test_list_attempts_keyset_pagination(async_session: AsyncSession) -> None:
    for i in range(3):
        _attempt(async_session, f"e{i}", ts=_TS + timedelta(minutes=i))
    await async_session.commit()

    page1 = await list_attempts(async_session, _START, _END, _NONE, None, None, 2)
    assert [a.event_id for a in page1.items] == ["e2", "e1"]  # newest first
    assert page1.next_cursor is not None

    page2 = await list_attempts(async_session, _START, _END, _NONE, None, page1.next_cursor, 2)
    assert [a.event_id for a in page2.items] == ["e0"]
    assert page2.next_cursor is None


async def test_list_attempts_filter_by_logical_request(async_session: AsyncSession) -> None:
    _attempt(async_session, "e1", logical_request_id="lr1")
    _attempt(async_session, "e2", logical_request_id="lr2")
    await async_session.commit()

    page = await list_attempts(async_session, _START, _END, _NONE, "lr1", None, 50)
    assert [a.event_id for a in page.items] == ["e1"]


async def test_request_detail_orders_attempts_and_totals(
    async_session: AsyncSession,
) -> None:
    _attempt(async_session, "e2", logical_request_id="lr1", sequence=1, input_tokens=200)
    _attempt(async_session, "e1", logical_request_id="lr1", sequence=0, input_tokens=100)
    _logical(async_session, "lr1", attempt_count=2, fallback_count=1, winning_attempt_id="e2")
    await async_session.commit()

    detail = await request_detail(async_session, "anthropic", "lr1")
    assert detail is not None
    assert [a.event_id for a in detail.attempts] == ["e1", "e2"]  # by sequence
    assert detail.request.winning_attempt_id == "e2"
    assert detail.request.total_tokens == 300  # summed from attempts
    assert detail.request.fallback_count == 1


async def test_request_detail_unknown_is_none(async_session: AsyncSession) -> None:
    assert await request_detail(async_session, "anthropic", "nope") is None


async def test_list_requests_fallback_filter(async_session: AsyncSession) -> None:
    _logical(async_session, "lr-plain", fallback_count=0)
    _logical(async_session, "lr-fb", fallback_count=2)
    await async_session.commit()

    page = await list_requests(async_session, _START, _END, None, True, None, 50)
    assert [r.logical_request_id for r in page.items] == ["lr-fb"]


async def test_sessions_scoped_identity_and_detail(async_session: AsyncSession) -> None:
    _attempt(async_session, "e1", session_id="sess-9", input_tokens=100)
    _attempt(async_session, "e2", session_id="sess-9", input_tokens=50)
    await async_session.commit()

    page = await list_sessions(async_session, _START, _END, _NONE, None, 50)
    assert len(page.items) == 1
    row = page.items[0]
    assert row.session_id == "sess-9" and row.attempt_count == 2
    assert row.total_tokens == 150
    assert row.scoped_id == scoped_session_id("anthropic", "", "sess-9")

    detail = await session_detail(async_session, "anthropic", "", "sess-9")
    assert detail is not None and detail.attempt_count == 2


async def test_session_detail_unknown_is_none(async_session: AsyncSession) -> None:
    assert await session_detail(async_session, "anthropic", "", "ghost") is None
