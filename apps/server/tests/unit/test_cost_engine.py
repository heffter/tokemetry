"""Unit tests for the cost engine."""

from datetime import UTC, date, datetime
from decimal import Decimal

from tokemetry_core.models import PriceRow, UsageEvent
from tokemetry_core.pricing.table import PricingTable
from tokemetry_server.providers import build_registry
from tokemetry_server.services.cost import CostEngine

_TS = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)

_PRICE = PriceRow(
    provider="anthropic",
    model="claude-opus-4-5",
    effective_date=date(2026, 1, 1),
    input_per_mtok=Decimal("5"),
    output_per_mtok=Decimal("25"),
    cache_read_per_mtok=Decimal("0.5"),
    cache_write_short_per_mtok=Decimal("6.25"),
    cache_write_long_per_mtok=Decimal("10"),
)


def _engine(rows: list[PriceRow]) -> CostEngine:
    return CostEngine(PricingTable(rows), build_registry())


def _event(
    model: str = "claude-opus-4-5",
    provider: str = "anthropic",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_short_tokens: int = 0,
    cache_write_long_tokens: int = 0,
) -> UsageEvent:
    return UsageEvent(
        event_id="req_1",
        provider=provider,
        native_model=model,
        ts=_TS,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_short_tokens=cache_write_short_tokens,
        cache_write_long_tokens=cache_write_long_tokens,
    )


def test_known_model_costs_computed() -> None:
    engine = _engine([_PRICE])
    cost = engine.cost(_event(input_tokens=1_000_000, output_tokens=200_000))
    assert cost == Decimal("10")  # 1M*5 + 0.2M*25 per MTok
    assert engine.unknown_models == frozenset()


def test_unknown_model_returns_none_and_is_recorded() -> None:
    engine = _engine([_PRICE])
    assert engine.cost(_event(model="claude-brand-new-9", input_tokens=100)) is None
    assert ("anthropic", "claude-brand-new-9") in engine.unknown_models


def test_unknown_provider_returns_none() -> None:
    engine = _engine([_PRICE])
    assert engine.cost(_event(provider="openai", model="gpt-5", input_tokens=100)) is None


def test_dated_model_resolves_to_undated_price() -> None:
    engine = _engine([_PRICE])
    cost = engine.cost(_event(model="claude-opus-4-5-20251101", input_tokens=1_000_000))
    assert cost == Decimal("5")
