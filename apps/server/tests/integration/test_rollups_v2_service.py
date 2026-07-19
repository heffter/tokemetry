"""Rollup service v2: cost split, grain, double-counting, idempotency, parity (66.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.computed_costs import record_cost
from tokemetry_server.services.cost_worker import sweep_uncosted_costs
from tokemetry_server.services.repricing import reprice
from tokemetry_server.services.rollups import refresh_rollups_for_days

_DAY = date(2026, 7, 10)
_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _event(session: AsyncSession, event_id: str, **fields: Any) -> models.UsageEventV2:
    row = make_v1_event(
        provider="anthropic", event_id=event_id, model="claude-sonnet-4-5",
        ts=_TS, **fields,
    )
    session.add(row)
    return row


async def _rate(session: AsyncSession, unit_type: str, price: str) -> None:
    session.add(
        models.RateCard(
            provider="anthropic", native_model="claude-sonnet-4-5", unit_type=unit_type,
            effective_from=date(2026, 1, 1), currency="USD", mode="realtime",
            unit_price=Decimal(price), source="default", priority=0, override=False,
            created_at=_TS,
        )
    )


async def _refresh(session: AsyncSession) -> int:
    return await refresh_rollups_for_days(session, "sqlite", [_DAY])


async def _rollups(session: AsyncSession) -> list[models.DailyRollup]:
    result = await session.execute(
        sa.select(models.DailyRollup).order_by(models.DailyRollup.billing_mode)
    )
    return list(result.scalars())


async def test_cost_split_by_status(async_session: AsyncSession) -> None:
    for eid in ("a:1", "a:2", "a:3"):
        _event(async_session, eid, input_tokens=1000)
    await async_session.flush()
    await record_cost(async_session, "anthropic", "a:1", amount=Decimal("0.005"),
                      cost_status="priced", pricing_version="1")
    await record_cost(async_session, "anthropic", "a:2", amount=Decimal("0.003"),
                      cost_status="partial", pricing_version="1")
    await record_cost(async_session, "anthropic", "a:3", amount=None,
                      cost_status="unpriced", pricing_version="1")
    await async_session.commit()

    await _refresh(async_session)
    await async_session.commit()

    (rollup,) = await _rollups(async_session)
    assert rollup.cost_priced_usd == Decimal("0.005")
    assert rollup.cost_partial_usd == Decimal("0.003")
    assert rollup.unpriced_event_count == 1
    assert rollup.cost_usd == Decimal("0.008")  # priced + partial


async def test_billing_mode_splits_grain_with_subscription_value(
    async_session: AsyncSession,
) -> None:
    _event(async_session, "a:1", input_tokens=1000)
    _event(async_session, "a:2", input_tokens=1000)
    await async_session.flush()
    await record_cost(async_session, "anthropic", "a:1", amount=Decimal("0.005"),
                      cost_status="priced", pricing_version="1", billing_mode="api_billed")
    await record_cost(async_session, "anthropic", "a:2", amount=None,
                      cost_status="priced", pricing_version="1", billing_mode="subscription",
                      subscription_equivalent_amount=Decimal("0.005"))
    await async_session.commit()

    await _refresh(async_session)
    await async_session.commit()

    rollups = await _rollups(async_session)
    assert len(rollups) == 2  # split by billing_mode
    api, sub = rollups  # ordered by billing_mode: api_billed, subscription
    assert api.billing_mode == "api_billed" and api.cost_priced_usd == Decimal("0.005")
    assert sub.billing_mode == "subscription"
    assert sub.cost_priced_usd is None and sub.subscription_value_usd == Decimal("0.005")


async def test_only_final_attempts_are_rolled_up(async_session: AsyncSession) -> None:
    _event(async_session, "final:1", input_tokens=1000)
    snapshot = make_v1_event(provider="anthropic", event_id="snap:1",
                             model="claude-sonnet-4-5", ts=_TS, input_tokens=500)
    snapshot.finality = "snapshot"
    async_session.add(snapshot)
    logical = make_v1_event(provider="anthropic", event_id="lr:1",
                            model="claude-sonnet-4-5", ts=_TS, input_tokens=9999)
    logical.event_kind = "logical_request"
    async_session.add(logical)
    await async_session.commit()

    await _refresh(async_session)
    await async_session.commit()

    (rollup,) = await _rollups(async_session)
    assert rollup.input_tokens == 1000  # snapshot and logical_request excluded


async def test_refresh_is_idempotent(async_session: AsyncSession) -> None:
    _event(async_session, "a:1", input_tokens=1000, cost_usd=Decimal("0.005"))
    await async_session.commit()
    await _refresh(async_session)
    await async_session.commit()
    await _refresh(async_session)  # second refresh must not accumulate
    await async_session.commit()

    (rollup,) = await _rollups(async_session)
    assert rollup.input_tokens == 1000
    assert rollup.cost_usd == Decimal("0.005")


async def test_v1_dataset_parity_via_transitional_cost(
    async_session: AsyncSession,
) -> None:
    # v1-shaped events with no computed_costs fall back to the transitional
    # cost_usd, so the rollup sums identically to the pre-migration algorithm.
    _event(async_session, "a:1", input_tokens=1000, output_tokens=200,
           cost_usd=Decimal("0.003"))
    _event(async_session, "a:2", input_tokens=500, cost_usd=Decimal("0.002"))
    await async_session.commit()
    await _refresh(async_session)
    await async_session.commit()

    (rollup,) = await _rollups(async_session)
    assert rollup.input_tokens == 1500 and rollup.output_tokens == 200
    assert rollup.cost_usd == Decimal("0.005")  # 0.003 + 0.002
    assert rollup.cost_priced_usd == Decimal("0.005")


async def test_reprice_refreshes_affected_rollups(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    _event(async_session, "a:1", input_tokens=1000)
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()
    await _refresh(async_session)
    await async_session.commit()

    (before,) = await _rollups(async_session)
    assert before.cost_priced_usd == Decimal("0.005000")

    # Raise the price and reprice: the rollup is refreshed for the affected day.
    (card,) = (
        await async_session.execute(sa.select(models.RateCard))
    ).scalars().all()
    card.unit_price = Decimal("0.000010")
    await async_session.commit()
    await reprice(async_session, "admin", datetime(2026, 7, 1, tzinfo=UTC),
                  datetime(2026, 8, 1, tzinfo=UTC))
    await async_session.commit()

    # The reprice refreshed the rollup via a Core upsert; drop the stale ORM
    # identity so the re-read reflects the persisted row.
    async_session.expire_all()
    (after,) = await _rollups(async_session)
    assert after.cost_priced_usd == Decimal("0.010000")  # rollup follows the reprice
