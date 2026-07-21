"""Shared audit log: record() and list_audit() (Task 70.4, service level)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.services.audit import list_audit, record

_T0 = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


async def test_record_writes_all_fields(async_session: AsyncSession) -> None:
    row = record(
        async_session,
        actor="admin",
        action="test_action",
        subject="thing",
        detail={"count": 3},
        ts=_T0,
        request_id="req-1",
    )
    await async_session.commit()
    assert row.id is not None
    assert row.actor == "admin"
    assert row.action == "test_action"
    assert row.subject == "thing"
    assert row.detail == {"count": 3}
    assert row.request_id == "req-1"


async def test_record_defaults_empty_detail(async_session: AsyncSession) -> None:
    row = record(async_session, actor=None, action="a", ts=_T0)
    await async_session.commit()
    assert row.detail == {}
    assert row.subject is None
    assert row.request_id is None


async def test_list_audit_filters_and_orders(async_session: AsyncSession) -> None:
    record(async_session, actor="alice", action="reprice", ts=_T0)
    record(async_session, actor="bob", action="reprice", ts=_T0 + timedelta(minutes=1))
    record(async_session, actor="alice", action="token_create", ts=_T0 + timedelta(minutes=2))
    await async_session.commit()

    # Newest first.
    everything = await list_audit(async_session)
    assert [r.action for r in everything] == ["token_create", "reprice", "reprice"]

    by_action = await list_audit(async_session, action="reprice")
    assert {r.actor for r in by_action} == {"alice", "bob"}

    by_actor = await list_audit(async_session, actor="alice")
    assert len(by_actor) == 2

    limited = await list_audit(async_session, limit=1)
    assert len(limited) == 1
