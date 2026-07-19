"""Built-in v2 pricing strategy wiring.

Registers the three provider plugins (anthropic, openai, zai) plus the generic
fallback onto a :class:`~tokemetry_core.registry.ProviderRegistry`. Registration
is explicit -- callers invoke :func:`register_pricing_strategies_v2` at startup,
mirroring the ``providers`` package's ``register`` convention. A process-wide
default registry is memoized for callers that only need pricing (the server cost
engine and workers) and would otherwise rebuild the same stateless strategies.
"""

from __future__ import annotations

import functools

from tokemetry_core.pricing.strategies.anthropic import AnthropicPricingStrategyV2
from tokemetry_core.pricing.strategies.generic import GenericPricingStrategyV2
from tokemetry_core.pricing.strategies.openai import OpenAIPricingStrategyV2
from tokemetry_core.pricing.strategies.zai import ZaiPricingStrategyV2
from tokemetry_core.registry import ProviderRegistry


def register_pricing_strategies_v2(registry: ProviderRegistry) -> None:
    """Register the built-in v2 pricing strategies on ``registry``.

    The generic strategy is registered as the default so any unregistered
    provider still resolves to a token-linear pricing (NFR-MAIN-002).
    """
    registry.register_pricing_v2(AnthropicPricingStrategyV2())
    registry.register_pricing_v2(OpenAIPricingStrategyV2())
    registry.register_pricing_v2(ZaiPricingStrategyV2())
    registry.register_pricing_v2(GenericPricingStrategyV2(), default=True)


@functools.cache
def default_v2_pricing_registry() -> ProviderRegistry:
    """Return a memoized registry with the built-in v2 pricing strategies.

    The strategies are stateless, so a single shared registry is safe. Used as
    the cost engine's default when no registry is injected.
    """
    registry = ProviderRegistry()
    register_pricing_strategies_v2(registry)
    return registry
