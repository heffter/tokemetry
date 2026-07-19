"""Anthropic v2 pricing strategy (port of the v1 semantics, FR-PRICE-010).

Anthropic bills uncached input, output, cache reads, and two distinct cache-write
TTL tiers (5-minute "short" and 1-hour "long"), so all five counters are emitted
as separate unit types. Anthropic does not bill reasoning ("thinking") tokens as
a separate line, so reasoning always folds into output (``FOLD_INTO_OUTPUT``);
this holds even if a ``reasoning_token`` rate were mistakenly configured.
"""

from __future__ import annotations

from tokemetry_core.interfaces import ProviderPricingStrategyV2, ReasoningBilling

#: Canonical provider id, matching ``ANTHROPIC_DESCRIPTOR.pricing_strategy``.
ANTHROPIC_PROVIDER = "anthropic"

#: The token unit types Anthropic bills: input, output, cache reads, and the two
#: cache-write TTL tiers kept distinct (FR-PRICE-010).
_ANTHROPIC_UNITS = frozenset(
    {
        "input_token",
        "output_token",
        "cache_read_token",
        "cache_write_short_token",
        "cache_write_long_token",
    }
)


class AnthropicPricingStrategyV2(ProviderPricingStrategyV2):
    """Prices Anthropic events: five token units, reasoning folded into output."""

    provider = ANTHROPIC_PROVIDER
    reasoning_billing = ReasoningBilling.FOLD_INTO_OUTPUT

    def emitted_token_units(self) -> frozenset[str]:
        """Return Anthropic's five billed token unit types."""
        return _ANTHROPIC_UNITS
