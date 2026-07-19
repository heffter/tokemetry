"""v2 grouped usage/cost aggregation service (Task 66.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from conftest import make_v1_event, seed_three_provider_registry
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.services.computed_costs import record_cost
from tokemetry_server.services.queries_v2 import (
    cost_reconciliation,
    grouped_costs,
    grouped_usage,
)
from tokemetry_server.services.query_framework import QueryFilters

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 1, tzinfo=UTC)
_END = datetime(2026, 8, 1, tzinfo=UTC)
_NONE = QueryFilters()


def _event(session: AsyncSession, event_id: str, **fields: Any) -> None:
    defaults: dict[str, Any] = {
        "provider": "anthropic", "model": "claude-sonnet-4-5",
        "machine": "m1", "session_id": "s1", "ts": _TS,
    }
    defaults.update(fields)
    session.add(make_v1_event(event_id=event_id, **defaults))


async def _cost(
    session: AsyncSession, event_id: str, *, provider: str = "anthropic",
    amount: Decimal | None, status: str = "priced", billing_mode: str = "api_billed",
    version: str = "1", sub: Decimal | None = None, observed: Decimal | None = None,
) -> None:
    row = await record_cost(
        session, provider, event_id, amount=amount, cost_status=status,
        pricing_version=version, billing_mode=billing_mode,
        subscription_equivalent_amount=sub,
    )
    if observed is not None:
        row.observed_cost = observed


async def test_grouped_usage_by_provider(async_session: AsyncSession) -> None:
    _event(async_session, "a:1", provider="anthropic", input_tokens=1000)
    _event(async_session, "a:2", provider="anthropic", input_tokens=500, output_tokens=200)
    _event(async_session, "o:1", provider="openai", input_tokens=300)
    await async_session.commit()

    rows = await grouped_usage(async_session, "provider", _START, _END, _NONE)
    by_key = {r.key: r for r in rows}
    assert by_key["anthropic"].input_tokens == 1500
    assert by_key["anthropic"].attempt_count == 2
    assert by_key["openai"].input_tokens == 300 and by_key["openai"].attempt_count == 1


async def test_grouped_usage_excludes_snapshots_and_logical(
    async_session: AsyncSession,
) -> None:
    _event(async_session, "a:1", input_tokens=1000)
    snap = make_v1_event(provider="anthropic", event_id="snap", model="claude-sonnet-4-5",
                         ts=_TS, input_tokens=999)
    snap.finality = "snapshot"
    async_session.add(snap)
    await async_session.commit()

    (row,) = await grouped_usage(async_session, "provider", _START, _END, _NONE)
    assert row.input_tokens == 1000 and row.attempt_count == 1


async def test_grouped_usage_dimensions(async_session: AsyncSession) -> None:
    _event(async_session, "a:1", machine="box-a", session_id="sess-1", input_tokens=10)
    _event(async_session, "a:2", machine="box-b", session_id="sess-1", input_tokens=20)
    await async_session.commit()

    machines = {r.key for r in await grouped_usage(async_session, "machine", _START, _END, _NONE)}
    assert machines == {"box-a", "box-b"}
    sessions = await grouped_usage(async_session, "session", _START, _END, _NONE)
    assert len(sessions) == 1 and sessions[0].input_tokens == 30


async def test_grouped_costs_keeps_dual_metrics_separate(
    async_session: AsyncSession,
) -> None:
    _event(async_session, "a:1", input_tokens=1000)
    _event(async_session, "a:2", input_tokens=1000)
    await async_session.flush()
    await _cost(async_session, "a:1", amount=Decimal("0.005"), status="priced")
    await _cost(async_session, "a:2", amount=None, status="priced",
                billing_mode="subscription", sub=Decimal("0.007"))
    await async_session.commit()

    (row,) = await grouped_costs(async_session, "provider", _START, _END, _NONE)
    assert row.actual_spend_usd == Decimal("0.005")
    assert row.subscription_value_usd == Decimal("0.007")
    # The two are distinct fields; there is no merged total on the row.
    assert not hasattr(row, "total_usd")


async def test_grouped_costs_status_split_and_mixed_version(
    async_session: AsyncSession,
) -> None:
    _event(async_session, "a:1", input_tokens=1000)
    _event(async_session, "a:2", input_tokens=1000)
    _event(async_session, "a:3", input_tokens=1000)
    await async_session.flush()
    await _cost(async_session, "a:1", amount=Decimal("0.005"), status="priced", version="1")
    await _cost(async_session, "a:2", amount=Decimal("0.003"), status="partial", version="2")
    await _cost(async_session, "a:3", amount=None, status="unpriced", version="1")
    await async_session.commit()

    (row,) = await grouped_costs(async_session, "provider", _START, _END, _NONE)
    assert row.cost_priced_usd == Decimal("0.005")
    assert row.cost_partial_usd == Decimal("0.003")
    assert row.unpriced_event_count == 1
    assert row.pricing_version == "mixed"  # spans versions 1 and 2


async def test_cost_reconciliation_reports_drift(async_session: AsyncSession) -> None:
    _event(async_session, "a:1", input_tokens=1000)
    await async_session.flush()
    await _cost(async_session, "a:1", amount=Decimal("0.005"), status="priced",
                observed=Decimal("0.006"))
    await async_session.commit()

    (row,) = await cost_reconciliation(async_session, _START, _END, _NONE)
    assert row.computed_usd == Decimal("0.005")
    assert row.observed_usd == Decimal("0.006")
    assert row.drift_usd == Decimal("0.001")


async def test_unknown_model_pseudo_filter(async_session: AsyncSession) -> None:
    await seed_three_provider_registry(async_session)  # registers claude-sonnet-4-5
    _event(async_session, "a:known", model="claude-sonnet-4-5", input_tokens=10)
    _event(async_session, "a:mystery", model="ghost-9", input_tokens=20)
    await async_session.commit()

    rows = await grouped_usage(
        async_session, "model", _START, _END, QueryFilters(unknown_model=True)
    )
    assert {r.key for r in rows} == {"ghost-9"}  # only the unregistered model


async def test_grouped_usage_by_day_is_cross_dialect(
    async_session: AsyncSession,
) -> None:
    """Day grouping buckets by UTC calendar day as YYYY-MM-DD.

    Regression for the SQLite CAST(ts_started AS DATE) failure: the key must be
    a portable ISO-day string, not a Date cast (which the SQLite Date result
    processor cannot parse).
    """
    _event(async_session, "d:1", ts=datetime(2026, 7, 10, 9, 0, tzinfo=UTC), input_tokens=1000)
    _event(async_session, "d:2", ts=datetime(2026, 7, 10, 18, 0, tzinfo=UTC), input_tokens=500)
    _event(async_session, "d:3", ts=datetime(2026, 7, 12, 1, 0, tzinfo=UTC), input_tokens=300)
    await async_session.commit()

    rows = await grouped_usage(async_session, "day", _START, _END, _NONE)
    by_key = {r.key: r for r in rows}
    assert set(by_key) == {"2026-07-10", "2026-07-12"}
    assert by_key["2026-07-10"].input_tokens == 1500
    assert by_key["2026-07-12"].input_tokens == 300


async def test_grouped_costs_by_day_is_cross_dialect(
    async_session: AsyncSession,
) -> None:
    """Cost day grouping buckets actual spend by UTC calendar day (SQLite-safe)."""
    _event(async_session, "d:1", ts=datetime(2026, 7, 10, 9, 0, tzinfo=UTC), input_tokens=1000)
    _event(async_session, "d:2", ts=datetime(2026, 7, 12, 9, 0, tzinfo=UTC), input_tokens=500)
    await async_session.commit()
    await _cost(async_session, "d:1", amount=Decimal("0.50"))
    await _cost(async_session, "d:2", amount=Decimal("0.25"))
    await async_session.commit()

    rows = await grouped_costs(async_session, "day", _START, _END, _NONE)
    by_key = {r.key: r for r in rows}
    assert set(by_key) == {"2026-07-10", "2026-07-12"}
    assert by_key["2026-07-10"].actual_spend_usd == Decimal("0.50")
    assert by_key["2026-07-12"].actual_spend_usd == Decimal("0.25")
