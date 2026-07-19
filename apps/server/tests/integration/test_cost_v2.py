"""Cost engine v2: priced/partial/unpriced, additive fees, reasoning, versions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.services.cost_v2 import CostEngineV2
from tokemetry_server.services.pricing_v2 import bump_pricing_version

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
_SOURCE = SourceRef(type=SourceType.GATEWAY, name="proxy", version="1")


async def _rate(session: AsyncSession, unit_type: str, price: str, **overrides: Any) -> None:
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
    session.add(models.RateCard(**defaults))


def _event(event_id: str = "anthropic:req_1", **overrides: Any) -> UsageEventV2:
    defaults: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "source": _SOURCE,
    }
    defaults.update(overrides)
    return UsageEventV2.model_validate(defaults)


async def _cost(session: AsyncSession, event: UsageEventV2) -> models.ComputedCost | None:
    result = await CostEngineV2(session).compute_and_record(event)
    await session.commit()
    return result


async def test_priced(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await _rate(async_session, "output_token", "0.000015")
    await async_session.commit()
    cost = await _cost(async_session, _event(input_tokens=1_000_000, output_tokens=1000))
    assert cost is not None
    assert cost.cost_status == "priced"
    assert cost.amount == Decimal("5.015000")  # 5 + 0.015
    assert cost.pricing_version == "1"


async def test_partial_when_a_unit_lacks_a_rate(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await async_session.commit()
    cost = await _cost(async_session, _event(input_tokens=1000, output_tokens=500))
    assert cost is not None
    assert cost.cost_status == "partial"
    assert cost.missing_units == {"units": ["output_token"]}
    assert cost.amount == Decimal("0.005000")  # only input priced


async def test_unpriced_when_no_rates(async_session: AsyncSession) -> None:
    cost = await _cost(async_session, _event(input_tokens=1000, output_tokens=500))
    assert cost is not None
    assert cost.cost_status == "unpriced"
    assert cost.amount is None


async def test_additive_hosted_tool_fees(async_session: AsyncSession) -> None:
    await _rate(async_session, "output_token", "0.000010")
    await _rate(async_session, "web_search_request", "0.010000")
    await async_session.commit()
    event = _event(output_tokens=100, billable_units={"web_search_request": 3})
    cost = await _cost(async_session, event)
    assert cost is not None
    # 100 * 0.00001 + 3 * 0.01 = 0.001 + 0.03
    assert cost.amount == Decimal("0.031000")


async def test_reasoning_folds_into_output_without_rate(async_session: AsyncSession) -> None:
    await _rate(async_session, "output_token", "0.000010")
    await async_session.commit()
    event = _event(output_tokens=100, reasoning_tokens=50)
    cost = await _cost(async_session, event)
    assert cost is not None
    # reasoning folded into output: (100 + 50) * 0.00001
    assert cost.amount == Decimal("0.001500")


async def test_reasoning_priced_separately_with_rate(async_session: AsyncSession) -> None:
    # OpenAI-style provider (SEPARATE_IF_RATED): reasoning is priced at its own
    # reasoning_token rate when one is configured (FR-PRICE-011).
    oai = {"provider": "openai", "native_model": "gpt-5"}
    await _rate(async_session, "output_token", "0.000010", **oai)
    await _rate(async_session, "reasoning_token", "0.000004", **oai)
    await async_session.commit()
    event = _event("openai:req_1", output_tokens=100, reasoning_tokens=50, **oai)
    cost = await _cost(async_session, event)
    assert cost is not None
    # 100 * 0.00001 + 50 * 0.000004 = 0.001 + 0.0002
    assert cost.amount == Decimal("0.001200")


async def test_anthropic_folds_reasoning_even_with_rate(async_session: AsyncSession) -> None:
    # Anthropic never bills reasoning separately (FOLD_INTO_OUTPUT): a stray
    # reasoning_token rate is ignored and reasoning always folds into output.
    await _rate(async_session, "output_token", "0.000010")
    await _rate(async_session, "reasoning_token", "0.000004")
    await async_session.commit()
    event = _event(output_tokens=100, reasoning_tokens=50)
    cost = await _cost(async_session, event)
    assert cost is not None
    # folded despite the rate: (100 + 50) * 0.00001
    assert cost.amount == Decimal("0.001500")


async def test_openai_prices_cached_input_and_ignores_cache_write_tiers(
    async_session: AsyncSession,
) -> None:
    # OpenAI bills cached input as cache reads and has no cache-write TTL tiers,
    # so a stray cache_write count is not priced (FR-DIM-006); hosted-tool fees
    # are additive.
    for unit, price in (
        ("input_token", "0.000003"),
        ("cache_read_token", "0.0000015"),
        ("output_token", "0.000006"),
        ("web_search_request", "0.010000"),
    ):
        await _rate(async_session, unit, price, provider="openai", native_model="gpt-5")
    await async_session.commit()
    event = _event(
        "openai:req_2", provider="openai", native_model="gpt-5",
        input_tokens=1000, cache_read_tokens=500, output_tokens=200,
        cache_write_short_tokens=999,  # no OpenAI TTL write tier -> ignored
        billable_units={"web_search_request": 2},
    )
    cost = await _cost(async_session, event)
    assert cost is not None
    assert cost.cost_status == "priced"  # dropped cache-write does not make it partial
    # 1000*0.000003 + 500*0.0000015 + 200*0.000006 + 2*0.01
    assert cost.amount == Decimal("0.024950")


async def test_snapshot_is_not_priced(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await async_session.commit()
    cost = await _cost(async_session, _event(finality="snapshot", input_tokens=1000))
    assert cost is None
    count = await async_session.scalar(
        sa.select(sa.func.count()).select_from(models.ComputedCost)
    )
    assert count == 0


async def test_recompute_is_idempotent(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await async_session.commit()
    event = _event(input_tokens=1000)
    first = await _cost(async_session, event)
    second = await _cost(async_session, event)
    assert first is not None and second is not None
    assert first.amount == second.amount
    count = await async_session.scalar(
        sa.select(sa.func.count()).select_from(models.ComputedCost)
    )
    assert count == 1  # same pricing version upserts one row


async def test_pricing_version_bump_creates_new_row(async_session: AsyncSession) -> None:
    await _rate(async_session, "input_token", "0.000005")
    await async_session.commit()
    event = _event(input_tokens=1000)
    await _cost(async_session, event)
    await bump_pricing_version(async_session)
    await async_session.commit()
    cost = await _cost(async_session, event)
    assert cost is not None
    assert cost.pricing_version == "2"
    assert cost.active is True
