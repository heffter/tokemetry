"""Unit tests for pricing: LiteLLM transform, table resolution, strategy."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from tokemetry_core.models import PriceRow, UsageEvent
from tokemetry_core.pricing.anthropic import (
    DEFAULT_ANTHROPIC_PRICE_ROWS,
    AnthropicPricingStrategy,
)
from tokemetry_core.pricing.litellm import price_rows_from_litellm
from tokemetry_core.pricing.table import (
    PricingTable,
    UnknownModelError,
    apply_overrides,
    base_model_id,
)

_DAY = date(2026, 7, 1)

_LITELLM_FIXTURE: dict[str, Any] = {
    "claude-opus-4-5-20251101": {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 5e-06,
        "output_cost_per_token": 2.5e-05,
        "cache_creation_input_token_cost": 6.25e-06,
        "cache_read_input_token_cost": 5e-07,
    },
    "claude-fable-5": {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 7e-06,
        "output_cost_per_token": 3.5e-05,
        # No cache prices: fallback multipliers must apply.
    },
    "anthropic.claude-opus-4-5-20251101-v1:0": {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 5e-06,
        "output_cost_per_token": 2.5e-05,
    },
    "gpt-5": {
        "litellm_provider": "openai",
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 4e-06,
    },
    "sample_spec": "not-a-model-entry",
}


def _event(**overrides: object) -> UsageEvent:
    """Build an Anthropic usage event with token overrides."""
    defaults: dict[str, object] = {
        "event_id": "req_1",
        "provider": "anthropic",
        "native_model": "claude-opus-4-5-20251101",
        "ts": datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return UsageEvent.model_validate(defaults)


class TestLitellmTransform:
    """price_rows_from_litellm behavior."""

    def test_keeps_only_canonical_provider_models(self) -> None:
        rows = price_rows_from_litellm(_LITELLM_FIXTURE, _DAY)
        assert {row.model for row in rows} == {
            "claude-opus-4-5-20251101",
            "claude-fable-5",
        }

    def test_converts_per_token_to_per_mtok(self) -> None:
        rows = {row.model: row for row in price_rows_from_litellm(_LITELLM_FIXTURE, _DAY)}
        opus = rows["claude-opus-4-5-20251101"]
        assert opus.input_per_mtok == Decimal("5")
        assert opus.output_per_mtok == Decimal("25")
        assert opus.cache_write_short_per_mtok == Decimal("6.25")
        assert opus.cache_read_per_mtok == Decimal("0.5")

    def test_missing_cache_prices_fall_back_to_multipliers(self) -> None:
        rows = {row.model: row for row in price_rows_from_litellm(_LITELLM_FIXTURE, _DAY)}
        fable = rows["claude-fable-5"]
        assert fable.cache_write_short_per_mtok == Decimal("8.75")  # 7 * 1.25
        assert fable.cache_write_long_per_mtok == Decimal("14")  # 7 * 2
        assert fable.cache_read_per_mtok == Decimal("0.7")  # 7 * 0.1

    def test_missing_long_write_derived_when_short_present(self) -> None:
        rows = {row.model: row for row in price_rows_from_litellm(_LITELLM_FIXTURE, _DAY)}
        opus = rows["claude-opus-4-5-20251101"]
        assert opus.cache_write_long_per_mtok == Decimal("10")  # 5 * 2


class TestBaseModelId:
    """Dated-suffix stripping."""

    def test_strips_date_suffix(self) -> None:
        assert base_model_id("claude-opus-4-5-20251101") == "claude-opus-4-5"

    def test_keeps_undated_ids(self) -> None:
        assert base_model_id("claude-fable-5") == "claude-fable-5"


class TestPricingTable:
    """Resolution by model id and date."""

    def _table(self) -> PricingTable:
        return PricingTable(list(DEFAULT_ANTHROPIC_PRICE_ROWS))

    def test_exact_match(self) -> None:
        row = self._table().resolve("anthropic", "claude-opus-4-5", _DAY)
        assert row.input_per_mtok == Decimal("5")

    def test_dated_query_matches_undated_row(self) -> None:
        row = self._table().resolve("anthropic", "claude-opus-4-5-20251101", _DAY)
        assert row.model == "claude-opus-4-5"

    def test_undated_query_matches_dated_row(self) -> None:
        table = PricingTable(price_rows_from_litellm(_LITELLM_FIXTURE, _DAY))
        row = table.resolve("anthropic", "claude-opus-4-5", _DAY)
        assert row.model == "claude-opus-4-5-20251101"

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(UnknownModelError):
            self._table().resolve("anthropic", "claude-nonexistent-9", _DAY)

    def test_date_versioning_picks_latest_not_after(self) -> None:
        old = DEFAULT_ANTHROPIC_PRICE_ROWS[0]
        new = old.model_copy(
            update={
                "effective_date": date(2026, 6, 1),
                "input_per_mtok": Decimal("4"),
            }
        )
        table = PricingTable([old, new])

        assert table.resolve("anthropic", old.model, date(2026, 5, 1)).input_per_mtok == Decimal(
            "5"
        )
        assert table.resolve("anthropic", old.model, date(2026, 7, 1)).input_per_mtok == Decimal(
            "4"
        )

    def test_no_row_effective_yet_raises(self) -> None:
        with pytest.raises(UnknownModelError):
            self._table().resolve("anthropic", "claude-opus-4-5", date(2020, 1, 1))

    def test_models_listing(self) -> None:
        assert "claude-opus-4-5" in self._table().models("anthropic")


class TestOverrides:
    """Manual price overrides."""

    def test_override_replaces_named_fields(self) -> None:
        rows = apply_overrides(
            list(DEFAULT_ANTHROPIC_PRICE_ROWS),
            {"claude-opus-4-5": {"input_per_mtok": "9.99"}},
        )
        opus = next(row for row in rows if row.model == "claude-opus-4-5")
        assert opus.input_per_mtok == Decimal("9.99")
        assert opus.output_per_mtok == Decimal("25")

    def test_non_matching_models_untouched(self) -> None:
        rows = apply_overrides(list(DEFAULT_ANTHROPIC_PRICE_ROWS), {"other-model": {}})
        assert rows == list(DEFAULT_ANTHROPIC_PRICE_ROWS)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-price"):
            apply_overrides(
                list(DEFAULT_ANTHROPIC_PRICE_ROWS),
                {"claude-opus-4-5": {"model": "hijack"}},
            )


class TestAnthropicStrategy:
    """Cost formula verification."""

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

    def test_formula(self) -> None:
        event = _event(
            input_tokens=1_000_000,
            output_tokens=200_000,
            cache_read_tokens=4_000_000,
            cache_write_short_tokens=100_000,
            cache_write_long_tokens=50_000,
        )
        # 1M*5 + 0.2M*25 + 4M*0.5 + 0.1M*6.25 + 0.05M*10 per MTok
        expected = Decimal("5") + Decimal("5") + Decimal("2") + Decimal("0.625") + Decimal("0.5")

        assert AnthropicPricingStrategy().cost(event, self._PRICE) == expected

    def test_zero_event_costs_nothing(self) -> None:
        assert AnthropicPricingStrategy().cost(_event(), self._PRICE) == Decimal("0")

    def test_quantized_to_micro_usd(self) -> None:
        cost = AnthropicPricingStrategy().cost(_event(input_tokens=1), self._PRICE)
        assert cost == Decimal("0.000005")
