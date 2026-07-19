"""Billing modes on computed_costs and dual cost metrics (D-007, FR-COST-011/012).

The cost engine stamps each computed_costs row with a billing mode resolved from
the event's source (with an account-level machine override): api_billed rows
populate ``amount`` (real spend), subscription rows populate
``subscription_equivalent_amount`` with ``amount`` null (imputed value). The dual
metrics report the two as separate sums that are never merged.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.billing_mode import API_BILLED, SUBSCRIPTION
from tokemetry_server.services.cost_queries import DualCostMetrics, dual_cost_metrics
from tokemetry_server.services.cost_worker import sweep_uncosted_costs

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


async def _rate(session: AsyncSession, unit_type: str, price: str) -> None:
    session.add(
        models.RateCard(
            provider="anthropic",
            native_model="claude-sonnet-4-5",
            unit_type=unit_type,
            effective_from=date(2026, 1, 1),
            currency="USD",
            mode="realtime",
            unit_price=Decimal(price),
            source="default",
            priority=0,
            override=False,
            created_at=_TS,
        )
    )


async def _source(session: AsyncSession, billing_mode: str, name: str) -> int:
    src = models.Source(
        type="gateway",
        name=name,
        instance_id=name,
        billing_mode=billing_mode,
        first_seen=_TS,
        last_seen=_TS,
        revoked=False,
    )
    session.add(src)
    await session.flush()
    return src.id


def _event(session: AsyncSession, event_id: str, **fields: Any) -> None:
    session.add(
        make_v1_event(
            provider="anthropic",
            event_id=event_id,
            model="claude-sonnet-4-5",
            ts=_TS,
            **fields,
        )
    )


async def _active(session: AsyncSession, event_id: str) -> models.ComputedCost:
    result = await session.execute(
        sa.select(models.ComputedCost).where(
            models.ComputedCost.event_id == event_id,
            models.ComputedCost.active.is_(True),
        )
    )
    return result.scalar_one()


async def test_api_billed_source_populates_amount(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    sid = await _source(async_session, API_BILLED, "gw-api")
    _event(async_session, "anthropic:api", input_tokens=1000, source_id=sid)
    await async_session.commit()

    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    cost = await _active(async_session, "anthropic:api")
    assert cost.billing_mode == API_BILLED
    assert cost.amount == Decimal("0.005000")
    assert cost.subscription_equivalent_amount is None


async def test_subscription_source_populates_equivalent_not_amount(
    async_session: AsyncSession,
) -> None:
    await _rate(async_session, "input_token", "0.000005")
    sid = await _source(async_session, SUBSCRIPTION, "gw-sub")
    _event(async_session, "anthropic:sub", input_tokens=1000, source_id=sid)
    await async_session.commit()

    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    cost = await _active(async_session, "anthropic:sub")
    assert cost.billing_mode == SUBSCRIPTION
    assert cost.amount is None  # no real spend
    assert cost.subscription_equivalent_amount == Decimal("0.005000")
    assert cost.cost_status == "priced"  # the equivalent was computed


async def test_machine_override_marks_collector_events_subscription(
    async_session: AsyncSession,
) -> None:
    # A collector source defaults to api_billed; the account-level machine
    # override values a subscription (Max) machine's usage as subscription.
    await _rate(async_session, "input_token", "0.000005")
    sid = await _source(async_session, API_BILLED, "collector")
    _event(async_session, "anthropic:m", input_tokens=1000, source_id=sid, machine="maxbook")
    await async_session.commit()

    await sweep_uncosted_costs(async_session, billing_mode_overrides={"maxbook": SUBSCRIPTION})
    await async_session.commit()

    cost = await _active(async_session, "anthropic:m")
    assert cost.billing_mode == SUBSCRIPTION
    assert cost.amount is None
    assert cost.subscription_equivalent_amount == Decimal("0.005000")


async def test_dual_metrics_report_separate_sums_never_merged(
    async_session: AsyncSession,
) -> None:
    await _rate(async_session, "input_token", "0.000005")
    api_sid = await _source(async_session, API_BILLED, "gw-api")
    sub_sid = await _source(async_session, SUBSCRIPTION, "gw-sub")
    _event(async_session, "anthropic:api", input_tokens=1000, source_id=api_sid)
    _event(async_session, "anthropic:sub", input_tokens=2000, source_id=sub_sid)
    await async_session.commit()

    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    metrics = await dual_cost_metrics(async_session)
    assert metrics.actual_spend_usd == Decimal("0.005")  # 1000 * 0.000005
    assert metrics.subscription_value_usd == Decimal("0.010")  # 2000 * 0.000005
    # The invariant: the two are never combined into a single total.
    field_names = {f.name for f in dataclasses.fields(DualCostMetrics)}
    assert field_names == {"actual_spend_usd", "subscription_value_usd"}


async def test_dual_metrics_respects_time_range(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    api_sid = await _source(async_session, API_BILLED, "gw-api")
    _event(async_session, "anthropic:api", input_tokens=1000, source_id=api_sid)
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    inside = await dual_cost_metrics(
        async_session, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC)
    )
    assert inside.actual_spend_usd == Decimal("0.005")
    outside = await dual_cost_metrics(
        async_session, datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
    )
    assert outside.actual_spend_usd == Decimal("0")
