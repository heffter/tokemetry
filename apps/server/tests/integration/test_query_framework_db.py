"""DB-backed query-framework tests: keyset stability and warnings (Task 66.3)."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import make_v1_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.computed_costs import record_cost
from tokemetry_server.services.query_framework import (
    Page,
    build_page,
    collect_warnings,
    decode_cursor,
    encode_cursor,
    keyset_condition,
)

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _dq(session: AsyncSession, subject: str) -> None:
    session.add(
        models.DataQualityEvent(kind="probe", subject=subject, detail={}, ts=_TS)
    )


async def _page(session: AsyncSession, cursor: str | None, limit: int = 2) -> Page:
    dq = models.DataQualityEvent
    stmt = select(dq).order_by(dq.subject, dq.id)
    if cursor is not None:
        value, row_id = decode_cursor(cursor)
        stmt = stmt.where(keyset_condition(dq.subject, dq.id, value, row_id, False))
    rows = (await session.execute(stmt.limit(limit + 1))).scalars().all()
    return build_page(list(rows), limit, lambda r: encode_cursor(r.subject, r.id))


async def test_keyset_pagination_is_stable_under_insert(
    async_session: AsyncSession,
) -> None:
    for subject in ("s0", "s1", "s2", "s3"):
        _dq(async_session, subject)
    await async_session.commit()

    page1 = await _page(async_session, None)
    assert [r.subject for r in page1.items] == ["s0", "s1"]

    # A row inserted after page 1, sorting after the cursor, must appear once and
    # must not skip or duplicate any original row.
    _dq(async_session, "s2b")
    await async_session.commit()

    page2 = await _page(async_session, page1.next_cursor)
    page3 = await _page(async_session, page2.next_cursor) if page2.next_cursor else Page([], None)

    seen = [r.subject for r in [*page1.items, *page2.items, *page3.items]]
    for subject in ("s0", "s1", "s2", "s3"):
        assert seen.count(subject) == 1  # no skips, no duplicates
    assert "s2b" in seen  # the concurrently-inserted row surfaces exactly once
    assert seen.count("s2b") == 1


async def test_collect_warnings_reports_each_condition(
    async_session: AsyncSession,
) -> None:
    # Unpriced event in range.
    async_session.add(
        make_v1_event(provider="anthropic", event_id="a:1", model="m", ts=_TS)
    )
    await async_session.flush()
    await record_cost(async_session, "anthropic", "a:1", amount=None,
                      cost_status="unpriced", pricing_version="1")
    # Unknown-model observation in range.
    async_session.add(
        models.DataQualityEvent(kind="unknown_model", subject="anthropic/m",
                                detail={"provider": "anthropic", "native_model": "m"}, ts=_TS)
    )
    # A stale source (never ingested successfully).
    async_session.add(
        models.Source(type="collector", name="old", instance_id="old",
                      billing_mode="api_billed", first_seen=_TS, last_seen=_TS,
                      revoked=False, last_successful_ingest=None)
    )
    await async_session.commit()

    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    stale_before = datetime(2026, 7, 9, tzinfo=UTC)
    warnings = await collect_warnings(async_session, start, end, stale_before)

    kinds = {w.kind: w.count for w in warnings}
    assert kinds == {"unpriced_events": 1, "unknown_models": 1, "stale_sources": 1}


async def test_collect_warnings_empty_when_clean(async_session: AsyncSession) -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    assert await collect_warnings(async_session, start, end) == []


async def test_unpriced_warning_respects_range(async_session: AsyncSession) -> None:
    out_of_range = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    async_session.add(
        make_v1_event(provider="anthropic", event_id="a:1", model="m", ts=out_of_range)
    )
    await async_session.flush()
    await record_cost(async_session, "anthropic", "a:1", amount=None,
                      cost_status="unpriced", pricing_version="1")
    await async_session.commit()

    warnings = await collect_warnings(
        async_session,
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert warnings == []  # the unpriced event is outside the queried range
