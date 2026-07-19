"""Transform LiteLLM's price database into v2 rate-card rows (FR-PRICE-021).

LiteLLM's ``model_prices_and_context_window.json`` mirrors provider pricing as
USD *per single token*, which is exactly the ``rate_cards`` unit-price grain, so
no per-MTok scaling is needed here (unlike the v1 ``PriceRow`` transform). For
each model, only the token unit types the provider's v2 strategy actually bills
are emitted (:meth:`~tokemetry_core.interfaces.ProviderPricingStrategyV2.emitted_token_units`),
so OpenAI's cached input becomes a ``cache_read_token`` rate without inventing
Anthropic cache-write TTL tiers it never bills (FR-DIM-006).

Anthropic cache multipliers relative to base input price -- 5-minute write 1.25x,
1-hour write 2x, read 0.1x -- are applied as fallbacks when the source lacks an
explicit cache price, so a model's emitted units always carry a rate.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from tokemetry_core.pricing.sources.rate_card import RateCardRow
from tokemetry_core.pricing.strategies.defaults import default_v2_pricing_registry

#: Fallback multipliers on base input price (Anthropic-documented ratios).
_SHORT_WRITE_MULTIPLIER = Decimal("1.25")
_LONG_WRITE_MULTIPLIER = Decimal("2")
_CACHE_READ_MULTIPLIER = Decimal("0.1")

#: unit_type -> (LiteLLM source key, fallback multiplier on base input | None).
#: input/output have no fallback: a model missing either is skipped entirely.
_UNIT_SOURCE: dict[str, tuple[str, Decimal | None]] = {
    "input_token": ("input_cost_per_token", None),
    "output_token": ("output_cost_per_token", None),
    "cache_read_token": ("cache_read_input_token_cost", _CACHE_READ_MULTIPLIER),
    "cache_write_short_token": ("cache_creation_input_token_cost", _SHORT_WRITE_MULTIPLIER),
    "cache_write_long_token": (
        "cache_creation_input_token_cost_above_1hr",
        _LONG_WRITE_MULTIPLIER,
    ),
}

#: LiteLLM providers this transform imports (Z.ai is not in LiteLLM; it comes
#: from the curated official source).
DEFAULT_LITELLM_PROVIDERS: tuple[str, ...] = ("anthropic", "openai")


def _per_token(value: Any) -> Decimal | None:
    """Read a per-token USD price from a source value, or None if absent."""
    if value is None:
        return None
    return Decimal(str(value))


def rate_cards_from_litellm(
    data: dict[str, Any],
    effective_from: date,
    verified_at: datetime | None = None,
    providers: Iterable[str] = DEFAULT_LITELLM_PROVIDERS,
) -> list[RateCardRow]:
    """Build ``litellm``-sourced rate-card rows from a LiteLLM price database.

    Args:
        data: Parsed ``model_prices_and_context_window.json`` content.
        effective_from: Date the resulting rows become effective.
        verified_at: When the source was fetched (stamped on every row).
        providers: LiteLLM provider ids to import.

    Returns:
        One row per (model, emitted unit type) for models that carry both an
        input and output price. Platform-prefixed ids (Bedrock ``anthropic....``)
        are skipped; only the provider's canonical ids are imported.
    """
    registry = default_v2_pricing_registry()
    wanted = set(providers)
    rows: list[RateCardRow] = []
    for model_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        provider = entry.get("litellm_provider")
        if provider not in wanted:
            continue
        if "." in model_id or "/" in model_id:
            continue  # platform-prefixed alias of a canonical id
        input_price = _per_token(entry.get("input_cost_per_token"))
        output_price = _per_token(entry.get("output_cost_per_token"))
        if input_price is None or output_price is None:
            continue
        emitted = registry.pricing_v2(provider).emitted_token_units()
        for unit_type, (source_key, multiplier) in _UNIT_SOURCE.items():
            if unit_type not in emitted:
                continue
            price = _per_token(entry.get(source_key))
            if price is None:
                if multiplier is None:
                    continue  # required base unit already validated above
                price = input_price * multiplier
            rows.append(
                RateCardRow(
                    provider=provider,
                    native_model=model_id,
                    unit_type=unit_type,
                    effective_from=effective_from,
                    unit_price=price,
                    source="litellm",
                    verified_at=verified_at,
                )
            )
    return rows
