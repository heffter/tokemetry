"""Async cost worker: eventual coverage, restart catch-up, batch limit.

The worker prices final attempt events that lack an active ``computed_costs``
row, out of the ingest path (FR-COST-009). These tests exercise the sweep over
ledger rows: it prices freshly ingested events, skips already-costed ones (so a
worker restart only catches up the backlog), honours the batch limit, and reads
non-token billable units for additive fees.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.cost_v2 import CostEngineV2
from tokemetry_server.services.cost_worker import billable_units_for, sweep_uncosted_costs

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


async def _rate(
    session: AsyncSession, unit_type: str, price: str, **overrides: Any
) -> models.RateCard:
    """Add and return a rate card for anthropic/claude-sonnet-4-5."""
    defaults: dict[str, Any] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": unit_type,
        "effective_from": date(2026, 1, 1),
        "currency": "USD",
        "mode": "realtime",
        "unit_price": Decimal(price),
        "source": "default",
        "priority": 0,
        "override": False,
        "created_at": _TS,
    }
    defaults.update(overrides)
    card = models.RateCard(**defaults)
    session.add(card)
    return card


def _seed_event(session: AsyncSession, event_id: str, **fields: Any) -> None:
    """Add a final attempt ledger row for anthropic/claude-sonnet-4-5."""
    session.add(
        make_v1_event(
            provider="anthropic",
            event_id=event_id,
            model="claude-sonnet-4-5",
            ts=_TS,
            **fields,
        )
    )


async def _costs(session: AsyncSession) -> list[models.ComputedCost]:
    """All computed-cost rows, ordered by id."""
    result = await session.execute(
        sa.select(models.ComputedCost).order_by(models.ComputedCost.id)
    )
    return list(result.scalars())


async def test_sweep_prices_uncosted_events(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await _rate(async_session, "output_token", "0.000015")
    _seed_event(async_session, "anthropic:req_1", input_tokens=1_000_000, output_tokens=1000)
    await async_session.commit()

    priced = await sweep_uncosted_costs(async_session)
    await async_session.commit()

    assert priced == 1
    (cost,) = await _costs(async_session)
    assert cost.cost_status == "priced"
    assert cost.amount == Decimal("5.015000")  # 5 + 0.015
    assert cost.pricing_version == "1"
    assert cost.active is True


async def test_second_sweep_finds_nothing_new(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    _seed_event(async_session, "anthropic:req_1", input_tokens=1000)
    _seed_event(async_session, "anthropic:req_2", input_tokens=2000)
    await async_session.commit()

    first = await sweep_uncosted_costs(async_session)
    await async_session.commit()
    second = await sweep_uncosted_costs(async_session)
    await async_session.commit()

    assert first == 2
    assert second == 0  # every final attempt already has an active cost
    assert len(await _costs(async_session)) == 2


async def test_restart_catches_up_only_the_backlog(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    _seed_event(async_session, "anthropic:req_1", input_tokens=1000)
    _seed_event(async_session, "anthropic:req_2", input_tokens=2000)
    await async_session.commit()

    # One event was costed before the worker "restarted"; the sweep must price
    # only the remaining uncosted event, not re-price the covered one.
    already = (
        await async_session.execute(
            sa.select(models.UsageEventV2).where(
                models.UsageEventV2.event_id == "anthropic:req_1"
            )
        )
    ).scalar_one()
    await CostEngineV2(async_session).compute_and_record_row(already, {})
    await async_session.commit()

    caught_up = await sweep_uncosted_costs(async_session)
    await async_session.commit()

    assert caught_up == 1
    assert len(await _costs(async_session)) == 2


async def test_batch_size_bounds_a_single_sweep(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    for i in range(3):
        _seed_event(async_session, f"anthropic:req_{i}", input_tokens=1000)
    await async_session.commit()

    first = await sweep_uncosted_costs(async_session, batch_size=2)
    await async_session.commit()
    second = await sweep_uncosted_costs(async_session, batch_size=2)
    await async_session.commit()

    assert first == 2  # bounded by the batch size
    assert second == 1  # remainder priced on the next sweep
    assert len(await _costs(async_session)) == 3


async def test_billable_units_feed_additive_fees(async_session: AsyncSession) -> None:
    await _rate(async_session, "output_token", "0.000010")
    await _rate(async_session, "web_search_request", "0.010000")
    _seed_event(async_session, "anthropic:req_1", output_tokens=100)
    async_session.add(
        models.BillableUnit(
            provider="anthropic",
            event_id="anthropic:req_1",
            unit_type="web_search_request",
            quantity=Decimal(3),
        )
    )
    await async_session.commit()

    units = await billable_units_for(async_session, "anthropic", "anthropic:req_1")
    assert units == {"web_search_request": 3.0}

    priced = await sweep_uncosted_costs(async_session)
    await async_session.commit()
    assert priced == 1
    (cost,) = await _costs(async_session)
    # 100 * 0.00001 + 3 * 0.01 = 0.001 + 0.03
    assert cost.amount == Decimal("0.031000")


async def test_snapshots_are_never_swept(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    event = make_v1_event(
        provider="anthropic",
        event_id="anthropic:req_1",
        model="claude-sonnet-4-5",
        ts=_TS,
        input_tokens=1000,
    )
    event.finality = "snapshot"
    async_session.add(event)
    await async_session.commit()

    priced = await sweep_uncosted_costs(async_session)
    await async_session.commit()

    assert priced == 0
    assert await _costs(async_session) == []
