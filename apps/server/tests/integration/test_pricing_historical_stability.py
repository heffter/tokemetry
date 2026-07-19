"""TOK-5 acceptance: historical cost stability, v1<->v2 equality, visibility.

The epic's evidence (AC-007, FR-PRICE-001/009, US-010):

1. Adding a new rate card effective today never changes an already-computed
   historical cost -- recomputation resolves the price effective at the event's
   own timestamp, so past amounts are bit-identical.
2. The v2 rate-card cost engine reproduces the v1 per-MTok cost to the last
   decimal for the same events (migration equality).
3. Unpriced events and unknown models are visible end to end through the
   reports.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import PriceRow, UsageEvent
from tokemetry_core.pricing.table import PricingTable
from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.providers import build_registry
from tokemetry_server.services.cost import CostEngine
from tokemetry_server.services.cost_v2 import CostEngineV2
from tokemetry_server.services.cost_worker import sweep_uncosted_costs
from tokemetry_server.services.pricing_admin import (
    close_rate_card,
    create_rate_card,
    unknown_models_report,
    unpriced_report,
)
from tokemetry_server.services.repricing import reprice

_NOW = datetime.now(UTC)
_SOURCE = SourceRef(type=SourceType.GATEWAY, name="proxy", version="1")

#: Equivalent v1 per-MTok prices and v2 per-token prices for one model.
_PER_MTOK = {
    "input": Decimal("5"),
    "output": Decimal("25"),
    "cache_read": Decimal("0.5"),
    "cache_write_short": Decimal("6.25"),
    "cache_write_long": Decimal("10"),
}
_UNIT_PER_TOKEN = {
    "input_token": Decimal("0.000005"),
    "output_token": Decimal("0.000025"),
    "cache_read_token": Decimal("0.0000005"),
    "cache_write_short_token": Decimal("0.00000625"),
    "cache_write_long_token": Decimal("0.00001"),
}


async def _seed_v2_rates(session: AsyncSession, effective_from: date) -> None:
    for unit_type, price in _UNIT_PER_TOKEN.items():
        session.add(
            models.RateCard(
                provider="anthropic",
                native_model="claude-opus-4-5",
                unit_type=unit_type,
                effective_from=effective_from,
                currency="USD",
                mode="realtime",
                unit_price=price,
                source="litellm",
                priority=0,
                override=False,
                created_at=_NOW,
            )
        )


def _v2_event(event_id: str, ts: datetime, **tokens: int) -> UsageEventV2:
    return UsageEventV2.model_validate(
        {
            "schema_version": 2,
            "event_id": event_id,
            "event_kind": "attempt",
            "finality": "final",
            "sequence": 1,
            "provider": "anthropic",
            "native_model": "claude-opus-4-5",
            "ts_started": ts,
            "source": _SOURCE,
            **tokens,
        }
    )


async def test_historical_cost_unchanged_after_new_price(
    async_session: AsyncSession,
) -> None:
    # A price effective from January, and a historical event in March.
    card = await create_rate_card(
        async_session, "admin", _NOW,
        provider="anthropic", native_model="claude-opus-4-5",
        unit_type="input_token", effective_from=date(2026, 1, 1),
        unit_price=Decimal("0.000005"),
    )
    async_session.add(
        make_v1_event(
            provider="anthropic", event_id="hist:1", model="claude-opus-4-5",
            ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC), input_tokens=1000,
        )
    )
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    snapshot = (await _active_amount(async_session, "hist:1"))
    assert snapshot == Decimal("0.005000")

    # The operator raises the price today: close the old card, open a new one.
    today = _NOW.date()
    await close_rate_card(async_session, "admin", card.id, today - timedelta(days=1), _NOW)
    await create_rate_card(
        async_session, "admin", _NOW,
        provider="anthropic", native_model="claude-opus-4-5",
        unit_type="input_token", effective_from=today,
        unit_price=Decimal("0.000010"),
    )
    await async_session.commit()

    # Recompute the whole range: the March event resolves the January price.
    await reprice(async_session, "admin", datetime(2026, 1, 1, tzinfo=UTC), _NOW)
    await async_session.commit()

    assert await _active_amount(async_session, "hist:1") == snapshot  # bit-identical


async def test_v2_rate_cards_reproduce_v1_legacy_costs(
    async_session: AsyncSession,
) -> None:
    price_row = PriceRow(
        provider="anthropic",
        model="claude-opus-4-5",
        effective_date=date(2026, 1, 1),
        input_per_mtok=_PER_MTOK["input"],
        output_per_mtok=_PER_MTOK["output"],
        cache_read_per_mtok=_PER_MTOK["cache_read"],
        cache_write_short_per_mtok=_PER_MTOK["cache_write_short"],
        cache_write_long_per_mtok=_PER_MTOK["cache_write_long"],
    )
    v1_engine = CostEngine(PricingTable([price_row]), build_registry())
    await _seed_v2_rates(async_session, date(2026, 1, 1))
    await async_session.commit()

    ts = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    fixtures = [
        {"input_tokens": 1000},
        {"input_tokens": 1234, "output_tokens": 567},
        {
            "input_tokens": 1000, "output_tokens": 500, "cache_read_tokens": 200,
            "cache_write_short_tokens": 100, "cache_write_long_tokens": 50,
        },
    ]
    engine = CostEngineV2(async_session)
    for index, tokens in enumerate(fixtures):
        v1_cost = v1_engine.cost(
            UsageEvent(
                event_id=f"eq:{index}", provider="anthropic",
                native_model="claude-opus-4-5", ts=ts, **tokens,
            )
        )
        v2 = await engine.compute_and_record(_v2_event(f"eq:{index}", ts, **tokens))
        assert v2 is not None
        assert v1_cost == v2.amount  # equal to the last decimal (FR-PRICE-009)


async def test_unpriced_and_unknown_model_are_visible(
    async_session: AsyncSession,
) -> None:
    # An event for a model with no rate card is priced 'unpriced' and surfaces.
    async_session.add(
        make_v1_event(
            provider="anthropic", event_id="gap:1", model="mystery-9",
            ts=datetime(2026, 7, 1, 12, 0, tzinfo=UTC), input_tokens=1000,
        )
    )
    async_session.add(
        models.DataQualityEvent(
            kind="unknown_model", subject="anthropic/mystery-9",
            detail={"provider": "anthropic", "native_model": "mystery-9"},
            ts=_NOW, resolved=False,
        )
    )
    await async_session.commit()
    await sweep_uncosted_costs(async_session)
    await async_session.commit()

    unpriced = await unpriced_report(async_session)
    assert any(r.native_model == "mystery-9" and r.cost_status == "unpriced" for r in unpriced)

    unknown = await unknown_models_report(async_session)
    assert any(r.native_model == "mystery-9" for r in unknown)


async def _active_amount(session: AsyncSession, event_id: str) -> Decimal | None:
    result = await session.execute(
        sa.select(models.ComputedCost).where(
            models.ComputedCost.event_id == event_id,
            models.ComputedCost.active.is_(True),
        )
    )
    return result.scalar_one().amount
