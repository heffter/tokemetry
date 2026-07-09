"""Anthropic pricing strategy and built-in default price rows.

The strategy is linear: the Anthropic cache economics (5-minute write
1.25x, 1-hour write 2x, cache read 0.1x of base input) are baked into the
price row columns when rows are built (see ``litellm.py``), so cost is a
plain dot product of token counts and per-MTok rates.

``UsageEvent.input_tokens`` for Anthropic is the uncached input count --
the Messages API reports cache reads and writes separately -- so no
subtraction is needed here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tokemetry_core.interfaces import PricingStrategy
from tokemetry_core.models import PriceRow, UsageEvent

ANTHROPIC_PROVIDER = "anthropic"

_MTOK = Decimal(1_000_000)
_CENT_MICRO = Decimal("0.000001")


class AnthropicPricingStrategy(PricingStrategy):
    """Prices Anthropic usage events from per-MTok price rows."""

    provider = ANTHROPIC_PROVIDER

    def cost(self, event: UsageEvent, price: PriceRow) -> Decimal:
        """Return the USD cost of ``event`` under ``price``.

        Implements: uncached input x input price + 5m cache writes x short
        write price + 1h cache writes x long write price + cache reads x
        read price + output x output price (all rates per MTok).
        """
        total = (
            event.input_tokens * price.input_per_mtok
            + event.cache_write_short_tokens * price.cache_write_short_per_mtok
            + event.cache_write_long_tokens * price.cache_write_long_per_mtok
            + event.cache_read_tokens * price.cache_read_per_mtok
            + event.output_tokens * price.output_per_mtok
        ) / _MTOK
        return total.quantize(_CENT_MICRO)


def _row(model: str, input_usd: str, output_usd: str) -> PriceRow:
    """Build a default row deriving cache rates from the base input price."""
    input_price = Decimal(input_usd)
    return PriceRow(
        provider=ANTHROPIC_PROVIDER,
        model=model,
        effective_date=date(2026, 1, 1),
        input_per_mtok=input_price,
        output_per_mtok=Decimal(output_usd),
        cache_read_per_mtok=input_price * Decimal("0.1"),
        cache_write_short_per_mtok=input_price * Decimal("1.25"),
        cache_write_long_per_mtok=input_price * Decimal("2"),
    )


#: Built-in snapshot of verified Anthropic prices (docs, 2026-07) so the
#: system prices common models before the first LiteLLM sync. Models absent
#: here and in synced data are ingested with null cost and raise an alert.
DEFAULT_ANTHROPIC_PRICE_ROWS: tuple[PriceRow, ...] = (
    _row("claude-opus-4-5", "5", "25"),
    _row("claude-opus-4-1", "15", "75"),
    _row("claude-sonnet-4-5", "3", "15"),
    _row("claude-haiku-4-5", "1", "5"),
)
