"""Z.ai (GLM) v2 pricing strategy (FR-PRICE-012).

Z.ai's GLM models price cached input tokens (normalized onto the
``cache_read_token`` counter) distinctly from fresh input; there are no
Anthropic-style cache-write TTL tiers, so those units are not emitted. GLM bills
reasoning tokens as output unless a dedicated ``reasoning_token`` rate is
configured (``SEPARATE_IF_RATED``), matching the provider's documented behavior.
"""

from __future__ import annotations

from tokemetry_core.interfaces import ProviderPricingStrategyV2, ReasoningBilling

#: Canonical provider id, matching ``ZAI_DESCRIPTOR.pricing_strategy``.
ZAI_PROVIDER = "zai"

#: Z.ai bills input, output, and cached input (as cache reads); no cache-write
#: TTL tiers.
_ZAI_UNITS = frozenset({"input_token", "output_token", "cache_read_token"})


class ZaiPricingStrategyV2(ProviderPricingStrategyV2):
    """Prices Z.ai (GLM) events: cached input as reads, reasoning as output-if-unrated."""

    provider = ZAI_PROVIDER
    reasoning_billing = ReasoningBilling.SEPARATE_IF_RATED

    def emitted_token_units(self) -> frozenset[str]:
        """Return Z.ai's billed token unit types (no cache-write TTL tiers)."""
        return _ZAI_UNITS
