"""Adapter: reconstruct v1 token-shaped price rows from v2 rate cards (Task 64.10).

The v1 SettingsView reads per-MTok :class:`~tokemetry_core.models.PriceRow`
values. As ``rate_cards`` becomes the source of truth, this adapter rebuilds
those rows from the active cards so the existing UI keeps working until Task 67
replaces it. For each ``(provider, native_model)`` with active cards on a date,
the best card per token unit (highest ``priority``, then ``override``, then
latest ``effective_from``) is scaled from per-token to per-MTok; a model missing
an input or output rate is skipped. Cache units without a card default to zero.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import PriceRow

from tokemetry_server.db import models

#: Per-token -> per-MTok scale (the v1 PriceRow unit).
_PER_MTOK = Decimal(1_000_000)

#: Token unit type -> the PriceRow field it fills.
_UNIT_FIELD: dict[str, str] = {
    "input_token": "input_per_mtok",
    "output_token": "output_per_mtok",
    "cache_read_token": "cache_read_per_mtok",
    "cache_write_short_token": "cache_write_short_per_mtok",
    "cache_write_long_token": "cache_write_long_per_mtok",
}


def _rank(card: models.RateCard) -> tuple[int, int, date]:
    """Precedence for picking one card per unit (higher wins)."""
    return (card.priority, 1 if card.override else 0, card.effective_from)


async def price_rows_from_rate_cards(
    session: AsyncSession, on_date: date
) -> list[PriceRow]:
    """Reconstruct per-MTok price rows from the rate cards active on ``on_date``."""
    card = models.RateCard
    stmt = select(card).where(
        card.effective_from <= on_date,
        or_(card.effective_to.is_(None), card.effective_to >= on_date),
    )
    best: dict[tuple[str, str], dict[str, models.RateCard]] = {}
    for row in (await session.execute(stmt)).scalars():
        if row.unit_type not in _UNIT_FIELD:
            continue
        units = best.setdefault((row.provider, row.native_model), {})
        current = units.get(row.unit_type)
        if current is None or _rank(row) > _rank(current):
            units[row.unit_type] = row

    rows: list[PriceRow] = []
    for (provider, native_model), units in best.items():
        if "input_token" not in units or "output_token" not in units:
            continue

        def _price(unit_type: str, units: dict[str, models.RateCard] = units) -> Decimal:
            card_row = units.get(unit_type)
            return card_row.unit_price * _PER_MTOK if card_row is not None else Decimal(0)

        rows.append(
            PriceRow(
                provider=provider,
                model=native_model,
                effective_date=on_date,
                input_per_mtok=_price("input_token"),
                output_per_mtok=_price("output_token"),
                cache_read_per_mtok=_price("cache_read_token"),
                cache_write_short_per_mtok=_price("cache_write_short_token"),
                cache_write_long_per_mtok=_price("cache_write_long_token"),
            )
        )
    return rows
