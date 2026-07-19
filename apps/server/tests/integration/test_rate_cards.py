"""Rate-card table, lossless pricing expansion, and constraint tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tokemetry_server.db import models
from tokemetry_server.db.pricing_migration import (
    PER_MTOK,
    UNIT_PRICE_COLUMNS,
    expand_pricing_to_rate_cards,
)

_TS = datetime(2026, 7, 1, tzinfo=UTC)


def _pricing_row(**overrides: object) -> models.Pricing:
    defaults: dict[str, object] = {
        "provider": "anthropic",
        "model": "claude-opus-4-5",
        "effective_date": date(2026, 1, 1),
        "input_per_mtok": Decimal("5"),
        "output_per_mtok": Decimal("25"),
        "cache_read_per_mtok": Decimal("0.5"),
        "cache_write_short_per_mtok": Decimal("6.25"),
        "cache_write_long_per_mtok": Decimal("10"),
        "source": "default",
    }
    defaults.update(overrides)
    return models.Pricing(**defaults)


def _rate_card(**overrides: object) -> models.RateCard:
    defaults: dict[str, object] = {
        "provider": "anthropic",
        "native_model": "claude-opus-4-5",
        "unit_type": "input_token",
        "effective_from": date(2026, 1, 1),
        "currency": "USD",
        "mode": "realtime",
        "unit_price": Decimal("0.000005"),
        "source": "default",
        "priority": 0,
        "override": False,
        "created_at": _TS,
    }
    defaults.update(overrides)
    return models.RateCard(**defaults)


def test_rate_card_round_trip(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            _rate_card(
                unit_type="output_token",
                service_tier="standard",
                context_bracket="gt200k",
                verified_at=_TS,
            )
        )
        session.commit()
    with Session(migrated_engine) as session:
        row = session.execute(sa.select(models.RateCard)).scalar_one()
        assert row.unit_type == "output_token"
        assert row.service_tier == "standard"
        assert row.context_bracket == "gt200k"
        assert row.unit_price == Decimal("0.000005")


def test_expansion_is_lossless(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_pricing_row())
        session.commit()

    with migrated_engine.begin() as connection:
        inserted = expand_pricing_to_rate_cards(connection)
    assert inserted == 5

    with Session(migrated_engine) as session:
        cards = {
            card.unit_type: card
            for card in session.execute(sa.select(models.RateCard)).scalars()
        }
    original = _pricing_row()
    for unit_type, price_column in UNIT_PRICE_COLUMNS.items():
        # unit_price * 1e6 reproduces the original per-MTok price exactly.
        reproduced = cards[unit_type].unit_price * PER_MTOK
        assert reproduced == getattr(original, price_column)


def test_duplicate_grain_rejected(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_rate_card(service_tier="standard", context_bracket="gt200k"))
        session.commit()
    with Session(migrated_engine) as session:
        session.add(
            _rate_card(
                service_tier="standard",
                context_bracket="gt200k",
                unit_price=Decimal("0.000009"),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_priority_distinguishes_grain(migrated_engine: sa.Engine) -> None:
    """Two cards differing only by priority coexist (an override layer)."""
    with Session(migrated_engine) as session:
        session.add(_rate_card(priority=0))
        session.add(_rate_card(priority=10, override=True, unit_price=Decimal("0.000006")))
        session.commit()
        count = session.scalar(sa.select(sa.func.count()).select_from(models.RateCard))
        assert count == 2


def test_pricing_table_untouched_by_expansion(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_pricing_row())
        session.commit()
    with migrated_engine.begin() as connection:
        expand_pricing_to_rate_cards(connection)
    with Session(migrated_engine) as session:
        pricing = session.execute(sa.select(models.Pricing)).scalar_one()
        assert pricing.input_per_mtok == Decimal("5")  # v1 pricing intact
