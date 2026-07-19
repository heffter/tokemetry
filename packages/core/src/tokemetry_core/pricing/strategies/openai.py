"""OpenAI v2 pricing strategy (FR-DIM-006, FR-PRICE-011).

OpenAI reports cached input tokens, which the collector normalizes onto the
``cache_read_token`` counter; OpenAI has no cache-write TTL tiers, so the
Anthropic 5m/1h cache-write units are deliberately not emitted (a single
cache-write concept must not be misrepresented as TTL categories, FR-DIM-006).
Reasoning tokens are priced as output unless a separate ``reasoning_token`` rate
is configured (``SEPARATE_IF_RATED``, FR-PRICE-011). Hosted-tool fees such as
``web_search_request`` and ``tool_call`` are additive via the event's billable
units, handled by the base strategy.
"""

from __future__ import annotations

from tokemetry_core.interfaces import ProviderPricingStrategyV2, ReasoningBilling

#: Canonical provider id, matching ``OPENAI_DESCRIPTOR.pricing_strategy``.
OPENAI_PROVIDER = "openai"

#: OpenAI bills input, output, and cached input (as cache reads); it has no
#: cache-write TTL tiers, so those units are intentionally excluded.
_OPENAI_UNITS = frozenset({"input_token", "output_token", "cache_read_token"})


class OpenAIPricingStrategyV2(ProviderPricingStrategyV2):
    """Prices OpenAI events: cached input as reads, reasoning as output-if-unrated."""

    provider = OPENAI_PROVIDER
    reasoning_billing = ReasoningBilling.SEPARATE_IF_RATED

    def emitted_token_units(self) -> frozenset[str]:
        """Return OpenAI's billed token unit types (no cache-write TTL tiers)."""
        return _OPENAI_UNITS
