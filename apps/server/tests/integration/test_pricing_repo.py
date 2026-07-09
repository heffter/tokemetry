"""Integration tests for pricing persistence and cost recomputation."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import PriceRow
from tokemetry_server.db import models
from tokemetry_server.providers import build_registry
from tokemetry_server.services.cost import CostEngine
from tokemetry_server.services.pricing_repo import (
    load_pricing_table,
    recompute_costs,
    seed_default_pricing,
    upsert_price_rows,
)

_ON = date(2026, 7, 1)


async def test_seed_and_load(async_session: AsyncSession) -> None:
    await seed_default_pricing(async_session, "sqlite")
    await async_session.commit()

    table = await load_pricing_table(async_session)
    row = table.resolve("anthropic", "claude-opus-4-5", _ON)
    assert row.input_per_mtok == Decimal("5")
    assert row.output_per_mtok == Decimal("25")


async def test_seed_is_idempotent(async_session: AsyncSession) -> None:
    await seed_default_pricing(async_session, "sqlite")
    await seed_default_pricing(async_session, "sqlite")
    await async_session.commit()

    table = await load_pricing_table(async_session)
    assert len(table.models("anthropic")) == 4


async def test_upsert_overrides_price(async_session: AsyncSession) -> None:
    await seed_default_pricing(async_session, "sqlite")
    override = PriceRow(
        provider="anthropic",
        model="claude-opus-4-5",
        effective_date=date(2026, 1, 1),
        input_per_mtok=Decimal("9.99"),
        output_per_mtok=Decimal("25"),
        cache_read_per_mtok=Decimal("1"),
        cache_write_short_per_mtok=Decimal("12"),
        cache_write_long_per_mtok=Decimal("20"),
    )
    await upsert_price_rows(async_session, "sqlite", [override], "override")
    await async_session.commit()

    table = await load_pricing_table(async_session)
    assert table.resolve("anthropic", "claude-opus-4-5", _ON).input_per_mtok == Decimal("9.99")


async def _add_event(session: AsyncSession, event_id: str, model: str, input_tokens: int) -> None:
    session.add(
        models.UsageEvent(
            provider="anthropic",
            event_id=event_id,
            ts=datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC),
            model=model,
            input_tokens=input_tokens,
            provenance="local_estimate",
        )
    )


async def test_recompute_fills_costs(async_session: AsyncSession) -> None:
    await seed_default_pricing(async_session, "sqlite")
    await _add_event(async_session, "req_1", "claude-opus-4-5", 1_000_000)
    await async_session.commit()

    table = await load_pricing_table(async_session)
    engine = CostEngine(table, build_registry())
    updated = await recompute_costs(async_session, engine)
    await async_session.commit()

    assert updated == 1
    row = await async_session.get(models.UsageEvent, ("anthropic", "req_1"))
    assert row is not None
    assert row.cost_usd == Decimal("5")  # 1M input tokens at $5/MTok


async def test_recompute_only_missing_skips_priced(async_session: AsyncSession) -> None:
    await seed_default_pricing(async_session, "sqlite")
    await _add_event(async_session, "req_1", "claude-opus-4-5", 1_000_000)
    await async_session.commit()
    table = await load_pricing_table(async_session)
    engine = CostEngine(table, build_registry())

    await recompute_costs(async_session, engine)
    await async_session.commit()
    second = await recompute_costs(async_session, engine, only_missing=True)

    assert second == 0


async def test_recompute_leaves_unknown_model_null(async_session: AsyncSession) -> None:
    await seed_default_pricing(async_session, "sqlite")
    await _add_event(async_session, "req_1", "claude-unknown-9", 1_000_000)
    await async_session.commit()

    table = await load_pricing_table(async_session)
    engine = CostEngine(table, build_registry())
    await recompute_costs(async_session, engine)
    await async_session.commit()

    row = await async_session.get(models.UsageEvent, ("anthropic", "req_1"))
    assert row is not None
    assert row.cost_usd is None
    assert ("anthropic", "claude-unknown-9") in engine.unknown_models
