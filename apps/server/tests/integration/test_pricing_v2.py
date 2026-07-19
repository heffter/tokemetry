"""Rate resolution precedence, date boundaries, fallback, and overlap."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.pricing_v2 import (
    OverlapError,
    check_overlap,
    resolve_rate,
)

_NOW = datetime(2026, 7, 1, tzinfo=UTC)


async def _card(session: AsyncSession, **overrides: Any) -> None:
    defaults: dict[str, Any] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": "input_token",
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "currency": "USD",
        "region": None,
        "service_tier": None,
        "mode": "realtime",
        "context_bracket": None,
        "unit_price": Decimal("0.000003"),
        "source": "default",
        "verified_at": None,
        "priority": 0,
        "override": False,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    session.add(models.RateCard(**defaults))


async def _price(session: AsyncSession, **kwargs: Any) -> Decimal | None:
    rate = await resolve_rate(
        session, "anthropic", kwargs.pop("model", "claude-sonnet-4-5"),
        kwargs.pop("unit_type", "input_token"), kwargs.pop("at", date(2026, 6, 1)), **kwargs
    )
    return rate.unit_price if rate else None


async def test_basic_resolution(async_session: AsyncSession) -> None:
    await _card(async_session)
    await async_session.commit()
    assert await _price(async_session) == Decimal("0.000003")


async def test_date_boundaries(async_session: AsyncSession) -> None:
    await _card(async_session, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 1))
    await async_session.commit()
    # Before effective_from: none.
    assert await _price(async_session, at=date(2025, 12, 31)) is None
    # Exactly on effective_from: matched.
    assert await _price(async_session, at=date(2026, 1, 1)) == Decimal("0.000003")
    # After effective_to: none.
    assert await _price(async_session, at=date(2026, 4, 1)) is None


async def test_tier_exact_beats_null_fallback(async_session: AsyncSession) -> None:
    await _card(async_session, service_tier=None, unit_price=Decimal("0.000003"))
    await _card(async_session, service_tier="priority", unit_price=Decimal("0.000006"))
    await async_session.commit()
    # A priority-tier request prefers the tier-specific card.
    assert await _price(async_session, tier="priority") == Decimal("0.000006")
    # An unknown tier falls back to the null-tier card.
    assert await _price(async_session, tier="batchy") == Decimal("0.000003")
    # No tier requested uses only the null-tier card.
    assert await _price(async_session, tier=None) == Decimal("0.000003")


async def test_priority_and_override(async_session: AsyncSession) -> None:
    await _card(async_session, priority=0, unit_price=Decimal("0.000003"))
    await _card(async_session, priority=10, unit_price=Decimal("0.000009"))
    await async_session.commit()
    assert await _price(async_session) == Decimal("0.000009")  # higher priority wins

    # An override at equal priority beats a non-override (different date range).
    await _card(
        async_session,
        priority=10,
        override=True,
        effective_from=date(2025, 1, 1),
        unit_price=Decimal("0.000007"),
    )
    await async_session.commit()
    assert await _price(async_session) == Decimal("0.000007")


async def test_dated_model_falls_back_to_base(async_session: AsyncSession) -> None:
    await _card(async_session, native_model="claude-sonnet-4-5")
    await async_session.commit()
    price = await _price(async_session, model="claude-sonnet-4-5-20250514")
    assert price == Decimal("0.000003")


async def test_unknown_model_returns_none(async_session: AsyncSession) -> None:
    await _card(async_session)
    await async_session.commit()
    assert await _price(async_session, model="gpt-mystery") is None


async def test_overlap_rejected_and_adjacent_allowed(async_session: AsyncSession) -> None:
    await _card(async_session, effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30))
    await async_session.commit()

    # Intersecting range on the same grain: rejected.
    with pytest.raises(OverlapError):
        await check_overlap(
            async_session, "anthropic", "claude-sonnet-4-5", "input_token", None,
            "realtime", None, 0, date(2026, 6, 1), None,
        )
    # Adjacent (non-intersecting) range: allowed.
    await check_overlap(
        async_session, "anthropic", "claude-sonnet-4-5", "input_token", None,
        "realtime", None, 0, date(2026, 7, 1), None,
    )
    # Different grain (different tier): allowed even if dates intersect.
    await check_overlap(
        async_session, "anthropic", "claude-sonnet-4-5", "input_token", "priority",
        "realtime", None, 0, date(2026, 1, 1), None,
    )
