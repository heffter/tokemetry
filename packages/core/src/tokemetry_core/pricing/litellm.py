"""Transform LiteLLM's price database into normalized price rows.

LiteLLM's ``model_prices_and_context_window.json`` is the community-standard
machine-readable mirror of provider pricing. This module is a pure
transformation (no HTTP): the server fetches the JSON and passes the parsed
dict here. Costs in the source are USD per single token; rows are per MTok.

Anthropic cache multipliers relative to base input price -- 5-minute cache
write 1.25x, 1-hour cache write 2x, cache read 0.1x -- are applied as
fallbacks when the source lacks explicit cache prices, so the resulting rows
always carry a complete set of rates and pricing strategies stay linear.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from tokemetry_core.models import PriceRow

_MTOK = Decimal(1_000_000)

#: Fallback multipliers on base input price (Anthropic-documented ratios).
_SHORT_WRITE_MULTIPLIER = Decimal("1.25")
_LONG_WRITE_MULTIPLIER = Decimal("2")
_CACHE_READ_MULTIPLIER = Decimal("0.1")


def _per_mtok(value: Any) -> Decimal | None:
    """Convert a per-token USD cost from the source into per-MTok Decimal."""
    if value is None:
        return None
    return (Decimal(str(value)) * _MTOK).normalize()


def price_rows_from_litellm(
    data: dict[str, Any],
    effective_date: date,
    provider: str = "anthropic",
) -> list[PriceRow]:
    """Build normalized price rows from a parsed LiteLLM price database.

    Args:
        data: The parsed ``model_prices_and_context_window.json`` content.
        effective_date: Date the resulting rows become effective.
        provider: Only entries whose ``litellm_provider`` matches are kept.

    Returns:
        One row per matching model that has both input and output prices.
        Entries with prefixed ids (for example ``anthropic.claude...`` from
        Bedrock) are skipped: only the provider's canonical ids are wanted.
    """
    rows: list[PriceRow] = []
    for model_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("litellm_provider") != provider:
            continue
        if "." in model_id or "/" in model_id:
            continue  # platform-prefixed alias of a canonical id
        input_price = _per_mtok(entry.get("input_cost_per_token"))
        output_price = _per_mtok(entry.get("output_cost_per_token"))
        if input_price is None or output_price is None:
            continue
        short_write = _per_mtok(entry.get("cache_creation_input_token_cost"))
        long_write = _per_mtok(entry.get("cache_creation_input_token_cost_above_1hr"))
        cache_read = _per_mtok(entry.get("cache_read_input_token_cost"))
        rows.append(
            PriceRow(
                provider=provider,
                model=model_id,
                effective_date=effective_date,
                input_per_mtok=input_price,
                output_per_mtok=output_price,
                cache_read_per_mtok=(
                    cache_read
                    if cache_read is not None
                    else input_price * _CACHE_READ_MULTIPLIER
                ),
                cache_write_short_per_mtok=(
                    short_write
                    if short_write is not None
                    else input_price * _SHORT_WRITE_MULTIPLIER
                ),
                cache_write_long_per_mtok=(
                    long_write
                    if long_write is not None
                    else input_price * _LONG_WRITE_MULTIPLIER
                ),
            )
        )
    return rows
