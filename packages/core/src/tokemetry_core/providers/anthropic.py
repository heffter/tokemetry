"""Anthropic provider registration.

Grows with each adapter: pricing now, the Claude Code JSONL usage source
and the OAuth limits source in their own tasks. Applications call
:func:`register` at startup to enable the provider.
"""

from __future__ import annotations

from tokemetry_core.pricing.anthropic import AnthropicPricingStrategy
from tokemetry_core.registry import ProviderRegistry


def register(registry: ProviderRegistry) -> None:
    """Register all available Anthropic adapters on ``registry``."""
    registry.register_pricing(AnthropicPricingStrategy())
