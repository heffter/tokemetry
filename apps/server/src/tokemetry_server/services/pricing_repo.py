"""Pricing persistence: seed defaults, load the table, and recompute costs.

The pricing table lives in the database (Grafana-visible, overridable) but
is loaded into an in-memory :class:`PricingTable` for the cost engine at
startup. These functions bridge the two.
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import PriceRow, UsageEvent
from tokemetry_core.pricing.anthropic import DEFAULT_ANTHROPIC_PRICE_ROWS
from tokemetry_core.pricing.table import PricingTable

from tokemetry_server.db import models
from tokemetry_server.db.upsert import pricing_upsert
from tokemetry_server.services.cost import CostEngine


def _price_row_dict(row: PriceRow, source: str) -> dict[str, object]:
    """Build a pricing table row dict from a core price row."""
    return {
        "provider": row.provider,
        "model": row.model,
        "effective_date": row.effective_date,
        "input_per_mtok": row.input_per_mtok,
        "output_per_mtok": row.output_per_mtok,
        "cache_read_per_mtok": row.cache_read_per_mtok,
        "cache_write_short_per_mtok": row.cache_write_short_per_mtok,
        "cache_write_long_per_mtok": row.cache_write_long_per_mtok,
        "source": source,
    }


async def upsert_price_rows(
    session: AsyncSession,
    dialect_name: str,
    rows: list[PriceRow],
    source: str,
) -> int:
    """Upsert price rows into the pricing table; return the count written."""
    if not rows:
        return 0
    dicts = [_price_row_dict(row, source) for row in rows]
    stmt = pricing_upsert(dialect_name, models.Pricing.__table__, dicts)
    await session.execute(stmt)
    return len(dicts)


async def seed_default_pricing(session: AsyncSession, dialect_name: str) -> int:
    """Insert the built-in default price rows (idempotent upsert)."""
    return await upsert_price_rows(
        session, dialect_name, list(DEFAULT_ANTHROPIC_PRICE_ROWS), "default"
    )


async def load_pricing_table(session: AsyncSession) -> PricingTable:
    """Load all price rows from the database into a PricingTable."""
    result = await session.execute(select(models.Pricing))
    table = PricingTable()
    for row in result.scalars():
        table.add(
            PriceRow(
                provider=row.provider,
                model=row.model,
                effective_date=row.effective_date,
                input_per_mtok=row.input_per_mtok,
                output_per_mtok=row.output_per_mtok,
                cache_read_per_mtok=row.cache_read_per_mtok,
                cache_write_short_per_mtok=row.cache_write_short_per_mtok,
                cache_write_long_per_mtok=row.cache_write_long_per_mtok,
            )
        )
    return table


def _event_from_row(row: models.UsageEvent) -> UsageEvent:
    """Reconstruct the minimal core event needed to recompute cost."""
    return UsageEvent(
        event_id=row.event_id,
        provider=row.provider,
        native_model=row.model,
        ts=row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=UTC),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens,
        cache_write_short_tokens=row.cache_write_short_tokens,
        cache_write_long_tokens=row.cache_write_long_tokens,
    )


async def recompute_costs(
    session: AsyncSession,
    engine: CostEngine,
    only_missing: bool = False,
) -> int:
    """Recompute ``cost_usd`` for stored events; return the count updated.

    Args:
        session: Active session (the caller owns the transaction).
        engine: Cost engine holding the current pricing table.
        only_missing: When true, only reprice events whose cost is null
            (for example after adding a price for a previously unknown
            model); otherwise reprice every event.
    """
    statement = select(models.UsageEvent)
    if only_missing:
        statement = statement.where(models.UsageEvent.cost_usd.is_(None))
    result = await session.execute(statement)

    updated = 0
    for row in result.scalars():
        new_cost = engine.cost(_event_from_row(row))
        if new_cost != row.cost_usd:
            row.cost_usd = new_cost
            updated += 1
    return updated
