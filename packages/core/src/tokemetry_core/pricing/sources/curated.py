"""Curated official price source (FR-PRICE-021).

Hand-verified per-token prices for models LiteLLM does not cover (notably Z.ai's
GLM family, which is absent from the LiteLLM database) or where the official
provider price should override the community mirror. Rows carry ``source
"official"`` and a higher ``priority`` than ``litellm`` rows, so the rate
resolver prefers them at the same grain (FR-PRICE-004 precedence). This is a
maintained snapshot -- like the v1 default price rows -- that operators update as
providers publish new prices; the import pipeline lands changes as audited diffs.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from tokemetry_core.pricing.sources.rate_card import RateCardRow

#: Priority for official rows, above the ``litellm`` default of 0 so official
#: prices win at the same grain (FR-PRICE-021).
OFFICIAL_PRIORITY = 100

#: Hand-verified per-token USD prices, ``(provider, native_model, {unit: price})``.
#: Z.ai GLM prices (a LiteLLM gap); values are a maintained snapshot in USD per
#: single token.
_CURATED: tuple[tuple[str, str, dict[str, str]], ...] = (
    (
        "zai",
        "glm-4.6",
        {
            "input_token": "0.0000006",
            "output_token": "0.0000022",
            "cache_read_token": "0.00000011",
        },
    ),
    (
        "zai",
        "glm-4.5-air",
        {
            "input_token": "0.0000002",
            "output_token": "0.0000011",
            "cache_read_token": "0.00000003",
        },
    ),
)


def curated_rate_cards(
    effective_from: date, verified_at: datetime | None = None
) -> list[RateCardRow]:
    """Build the curated official rate-card rows effective from a date."""
    rows: list[RateCardRow] = []
    for provider, native_model, prices in _CURATED:
        for unit_type, price in prices.items():
            rows.append(
                RateCardRow(
                    provider=provider,
                    native_model=native_model,
                    unit_type=unit_type,
                    effective_from=effective_from,
                    unit_price=Decimal(price),
                    source="official",
                    priority=OFFICIAL_PRIORITY,
                    verified_at=verified_at,
                )
            )
    return rows
