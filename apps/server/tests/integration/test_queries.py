"""Integration tests for read-model aggregation queries."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services import queries
from tokemetry_server.services.pricing_repo import seed_default_pricing

_DAY = date(2026, 7, 9)


async def _add_rollup(
    session: AsyncSession,
    model: str,
    machine: str,
    tokens: int,
    cost: Decimal | None,
    project: str = "proj",
    day: date = _DAY,
) -> None:
    session.add(
        models.DailyRollup(
            day=day,
            provider="anthropic",
            machine=machine,
            model=model,
            project=project,
            input_tokens=tokens,
            total_tokens=tokens,
            cost_usd=cost,
            provenance="derived",
        )
    )


async def _add_event(session: AsyncSession, event_id: str, session_id: str, tokens: int) -> None:
    session.add(
        models.UsageEvent(
            provider="anthropic",
            event_id=event_id,
            ts=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
            model="claude-opus-4-5",
            machine="box-1",
            session_id=session_id,
            input_tokens=tokens,
            provenance="local_estimate",
        )
    )


async def test_usage_grouped_by_model(async_session: AsyncSession) -> None:
    await _add_rollup(async_session, "claude-opus-4-5", "box-1", 100, Decimal("1"))
    await _add_rollup(async_session, "claude-sonnet-4-5", "box-1", 50, Decimal("0.5"))
    await async_session.commit()

    buckets = await queries.usage_grouped(async_session, "model", _DAY, _DAY)

    by_key = {bucket.key: bucket for bucket in buckets}
    assert by_key["claude-opus-4-5"].total_tokens == 100
    assert by_key["claude-sonnet-4-5"].cost_usd == Decimal("0.5")


async def test_usage_grouped_by_machine_filtered(async_session: AsyncSession) -> None:
    await _add_rollup(async_session, "claude-opus-4-5", "box-1", 100, None)
    await _add_rollup(async_session, "claude-opus-4-5", "box-2", 200, None)
    await async_session.commit()

    buckets = await queries.usage_grouped(
        async_session, "machine", _DAY, _DAY, machine="box-2"
    )

    assert len(buckets) == 1
    assert buckets[0].key == "box-2"
    assert buckets[0].total_tokens == 200


async def test_usage_grouped_by_session(async_session: AsyncSession) -> None:
    await _add_event(async_session, "e1", "sess-a", 10)
    await _add_event(async_session, "e2", "sess-a", 20)
    await _add_event(async_session, "e3", "sess-b", 5)
    await async_session.commit()

    buckets = await queries.usage_grouped(async_session, "session", _DAY, _DAY)

    by_key = {bucket.key: bucket.total_tokens for bucket in buckets}
    assert by_key["sess-a"] == 30
    assert by_key["sess-b"] == 5


async def test_unsupported_group_by_raises(async_session: AsyncSession) -> None:
    import pytest

    with pytest.raises(ValueError, match="unsupported group_by"):
        await queries.usage_grouped(async_session, "bogus", _DAY, _DAY)


async def test_list_sessions(async_session: AsyncSession) -> None:
    await _add_event(async_session, "e1", "sess-a", 10)
    await _add_event(async_session, "e2", "sess-b", 20)
    await async_session.commit()

    sessions = await queries.list_sessions(async_session)

    assert {s.session_id for s in sessions} == {"sess-a", "sess-b"}
    assert all(s.message_count == 1 for s in sessions)


async def test_list_machines(async_session: AsyncSession) -> None:
    async_session.add(models.Machine(id="box-1", platform="linux"))
    await _add_event(async_session, "e1", "sess-a", 100)
    await async_session.commit()

    machines = await queries.list_machines(async_session)

    assert len(machines) == 1
    assert machines[0].id == "box-1"
    assert machines[0].total_tokens == 100
    assert machines[0].event_count == 1


async def test_total_cost(async_session: AsyncSession) -> None:
    await _add_rollup(async_session, "claude-opus-4-5", "box-1", 100, Decimal("1.5"))
    await _add_rollup(async_session, "claude-sonnet-4-5", "box-1", 50, Decimal("0.5"))
    await async_session.commit()

    assert await queries.total_cost(async_session, _DAY, _DAY) == Decimal("2.0")


async def test_punch_card(async_session: AsyncSession) -> None:
    await _add_event(async_session, "e1", "sess-a", 100)  # 2026-07-09 is a Thursday, 12:00
    await async_session.commit()

    card = await queries.punch_card(async_session, _DAY, _DAY)

    assert card[(3, 12)] == 100  # weekday 3 = Thursday, hour 12


async def test_list_pricing(async_session: AsyncSession) -> None:
    await seed_default_pricing(async_session, "sqlite")
    await async_session.commit()

    rows = await queries.list_pricing(async_session)

    assert any(row.model == "claude-opus-4-5" for row in rows)
