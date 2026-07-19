"""Lossless expansion of v1 per-MTok pricing rows into v2 rate cards.

Each ``pricing`` row carries five per-MTok token prices; the v2 ``rate_cards``
grain stores one price per single billable unit. This module expands each
pricing row into five ``rate_cards`` rows, dividing each per-MTok price by
1,000,000 in exact ``Decimal`` (FR-PRICE-009) so the original per-MTok value is
reproducible with zero drift. The v1 ``pricing`` table is left untouched and
keeps serving ``/api/v1/pricing`` until equality is confirmed (task 64.11).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Connection

from tokemetry_server.db import models

#: v2 unit type -> the v1 per-MTok pricing column it expands from.
UNIT_PRICE_COLUMNS: dict[str, str] = {
    "input_token": "input_per_mtok",
    "output_token": "output_per_mtok",
    "cache_read_token": "cache_read_per_mtok",
    "cache_write_short_token": "cache_write_short_per_mtok",
    "cache_write_long_token": "cache_write_long_per_mtok",
}

#: Tokens per MTok; the exact Decimal divisor for the per-unit conversion.
PER_MTOK = Decimal(1_000_000)


def expand_pricing_to_rate_cards(connection: Connection) -> int:
    """Insert five rate_cards rows per pricing row; return the count inserted.

    Idempotent enough for a one-time migration: it reads the current ``pricing``
    rows and inserts their per-unit expansions. Prices convert exactly for any
    per-MTok value with up to four decimal places (all real provider prices).
    """
    pricing = models.Pricing.__table__
    rows = connection.execute(sa.select(pricing)).mappings().all()
    now = datetime.now(UTC)

    values: list[dict[str, object]] = []
    for row in rows:
        for unit_type, price_column in UNIT_PRICE_COLUMNS.items():
            values.append(
                {
                    "provider": row["provider"],
                    "native_model": row["model"],
                    "unit_type": unit_type,
                    "effective_from": row["effective_date"],
                    "effective_to": None,
                    "currency": "USD",
                    "region": None,
                    "service_tier": None,
                    "mode": "realtime",
                    "context_bracket": None,
                    "unit_price": row[price_column] / PER_MTOK,
                    "source": row["source"],
                    "verified_at": None,
                    "priority": 0,
                    "override": False,
                    "created_at": now,
                }
            )

    if values:
        connection.execute(sa.insert(models.RateCard), values)
    return len(values)
