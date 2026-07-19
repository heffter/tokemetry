"""Generic token-linear pricing strategy (NFR-MAIN-002, FR-PROVIDER-005).

The fallback for any provider without a dedicated plugin: it bills the five
standard token counters linearly and prices reasoning as output unless a
``reasoning_token`` rate is configured. This keeps an unregistered provider
priceable from its rate cards alone, so ingest never has to reject an unknown
provider for lack of a strategy.
"""

from __future__ import annotations

from tokemetry_core.interfaces import ProviderPricingStrategyV2, ReasoningBilling

#: Registry key for the fallback strategy (not a real provider id).
GENERIC_PROVIDER = "generic"

#: The standard token counters, priced linearly.
_GENERIC_UNITS = frozenset(
    {
        "input_token",
        "output_token",
        "cache_read_token",
        "cache_write_short_token",
        "cache_write_long_token",
    }
)


class GenericPricingStrategyV2(ProviderPricingStrategyV2):
    """Plain token-linear pricing for providers without a dedicated plugin."""

    provider = GENERIC_PROVIDER
    reasoning_billing = ReasoningBilling.SEPARATE_IF_RATED

    def emitted_token_units(self) -> frozenset[str]:
        """Return the five standard token unit types."""
        return _GENERIC_UNITS
