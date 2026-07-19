"""Rate-card price source transforms: LiteLLM and curated official (Task 64.9)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from tokemetry_core.pricing.sources.curated import OFFICIAL_PRIORITY, curated_rate_cards
from tokemetry_core.pricing.sources.litellm import rate_cards_from_litellm
from tokemetry_core.pricing.sources.rate_card import RateCardRow

_EFFECTIVE = date(2026, 7, 1)
_VERIFIED = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

_FIXTURE: dict[str, Any] = {
    "claude-opus-4-5": {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 5e-06,
        "output_cost_per_token": 2.5e-05,
        "cache_creation_input_token_cost": 6.25e-06,
        "cache_read_input_token_cost": 5e-07,
    },
    "claude-fable-5": {  # no cache prices -> fallback multipliers
        "litellm_provider": "anthropic",
        "input_cost_per_token": 7e-06,
        "output_cost_per_token": 3.5e-05,
    },
    "gpt-5": {
        "litellm_provider": "openai",
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 4e-06,
    },
    "anthropic.claude-opus-4-5-v1:0": {  # Bedrock alias -> skipped (dot in id)
        "litellm_provider": "anthropic",
        "input_cost_per_token": 5e-06,
        "output_cost_per_token": 2.5e-05,
    },
    "half-priced": {  # missing output price -> skipped
        "litellm_provider": "anthropic",
        "input_cost_per_token": 1e-06,
    },
    "sample_spec": "not-a-model-entry",  # non-dict -> skipped
}


def _price(rows: list[RateCardRow], model: str, unit_type: str) -> Decimal:
    return next(
        r.unit_price for r in rows if r.native_model == model and r.unit_type == unit_type
    )


def _units(rows: list[RateCardRow], model: str) -> set[str]:
    return {r.unit_type for r in rows if r.native_model == model}


def test_anthropic_emits_five_units_with_cache_and_fallbacks() -> None:
    rows = rate_cards_from_litellm(_FIXTURE, _EFFECTIVE, _VERIFIED)
    assert _units(rows, "claude-opus-4-5") == {
        "input_token", "output_token", "cache_read_token",
        "cache_write_short_token", "cache_write_long_token",
    }
    assert _price(rows, "claude-opus-4-5", "input_token") == Decimal("0.000005")
    assert _price(rows, "claude-opus-4-5", "cache_read_token") == Decimal("0.0000005")
    assert _price(rows, "claude-opus-4-5", "cache_write_short_token") == Decimal("0.00000625")
    # long write absent in source -> fallback 2x base input
    assert _price(rows, "claude-opus-4-5", "cache_write_long_token") == Decimal("0.00001")


def test_missing_cache_prices_use_fallback_multipliers() -> None:
    rows = rate_cards_from_litellm(_FIXTURE, _EFFECTIVE, _VERIFIED)
    base = Decimal("0.000007")
    assert _price(rows, "claude-fable-5", "cache_read_token") == base * Decimal("0.1")
    assert _price(rows, "claude-fable-5", "cache_write_short_token") == base * Decimal("1.25")
    assert _price(rows, "claude-fable-5", "cache_write_long_token") == base * Decimal("2")


def test_openai_has_no_cache_write_tiers() -> None:
    rows = rate_cards_from_litellm(_FIXTURE, _EFFECTIVE, _VERIFIED)
    # OpenAI bills input/output/cached-input only; no TTL cache-write tiers.
    assert _units(rows, "gpt-5") == {"input_token", "output_token", "cache_read_token"}
    assert _price(rows, "gpt-5", "cache_read_token") == Decimal("0.0000001")  # fallback


def test_prefixed_and_incomplete_and_nondict_entries_are_skipped() -> None:
    rows = rate_cards_from_litellm(_FIXTURE, _EFFECTIVE, _VERIFIED)
    models = {r.native_model for r in rows}
    assert models == {"claude-opus-4-5", "claude-fable-5", "gpt-5"}


def test_rows_carry_litellm_source_and_verified_at() -> None:
    rows = rate_cards_from_litellm(_FIXTURE, _EFFECTIVE, _VERIFIED)
    assert all(r.source == "litellm" and r.priority == 0 for r in rows)
    assert all(r.verified_at == _VERIFIED and r.effective_from == _EFFECTIVE for r in rows)


def test_zai_is_absent_from_litellm() -> None:
    rows = rate_cards_from_litellm(_FIXTURE, _EFFECTIVE, _VERIFIED)
    assert not any(r.provider == "zai" for r in rows)


def test_curated_supplies_zai_official_rows_at_higher_priority() -> None:
    rows = curated_rate_cards(_EFFECTIVE, _VERIFIED)
    assert {r.provider for r in rows} == {"zai"}
    assert all(r.source == "official" and r.priority == OFFICIAL_PRIORITY for r in rows)
    assert _units(rows, "glm-4.6") == {"input_token", "output_token", "cache_read_token"}
    assert _price(rows, "glm-4.6", "input_token") == Decimal("0.0000006")
