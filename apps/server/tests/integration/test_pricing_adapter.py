"""v1 token-shaped price-row adapter over v2 rate cards (Task 64.10)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.pricing_adapter import price_rows_from_rate_cards

_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_ON = date(2026, 7, 1)


def _card(session: AsyncSession, unit_type: str, price: str, **overrides: Any) -> None:
    defaults: dict[str, Any] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": unit_type,
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "currency": "USD",
        "mode": "realtime",
        "unit_price": Decimal(price),
        "source": "litellm",
        "priority": 0,
        "override": False,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    session.add(models.RateCard(**defaults))


async def test_reconstructs_per_mtok_price_row(async_session: AsyncSession) -> None:
    _card(async_session, "input_token", "0.000005")
    _card(async_session, "output_token", "0.000015")
    _card(async_session, "cache_read_token", "0.0000005")
    await async_session.commit()

    rows = await price_rows_from_rate_cards(async_session, _ON)
    assert len(rows) == 1
    row = rows[0]
    assert row.input_per_mtok == Decimal("5")  # 0.000005 * 1e6
    assert row.output_per_mtok == Decimal("15")
    assert row.cache_read_per_mtok == Decimal("0.5")
    assert row.cache_write_short_per_mtok == Decimal("0")  # no card -> zero


async def test_picks_highest_priority_per_unit(async_session: AsyncSession) -> None:
    _card(async_session, "input_token", "0.000005", priority=0)
    _card(async_session, "input_token", "0.000010", priority=100)
    _card(async_session, "output_token", "0.000015")
    await async_session.commit()

    rows = await price_rows_from_rate_cards(async_session, _ON)
    assert rows[0].input_per_mtok == Decimal("10")  # priority 100 wins


async def test_model_without_output_is_skipped(async_session: AsyncSession) -> None:
    _card(async_session, "input_token", "0.000005")  # no output rate
    await async_session.commit()
    assert await price_rows_from_rate_cards(async_session, _ON) == []


async def test_closed_cards_are_excluded(async_session: AsyncSession) -> None:
    _card(async_session, "input_token", "0.000005", effective_to=date(2026, 6, 30))
    _card(async_session, "output_token", "0.000015")
    await async_session.commit()
    # input card closed before _ON -> model lacks an input rate -> skipped.
    assert await price_rows_from_rate_cards(async_session, _ON) == []
