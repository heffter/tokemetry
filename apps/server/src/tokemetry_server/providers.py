"""Server-side provider registry construction.

The server needs pricing strategies (to compute cost) but not usage/limits
sources (those run in the collector). This builds a registry with every
production provider registered.
"""

from __future__ import annotations

from tokemetry_core.pricing.anthropic import AnthropicPricingStrategy
from tokemetry_core.registry import ProviderRegistry


def build_registry() -> ProviderRegistry:
    """Return a registry with all production provider pricing strategies."""
    registry = ProviderRegistry()
    registry.register_pricing(AnthropicPricingStrategy())
    return registry
