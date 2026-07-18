"""Anthropic provider registration.

Grows with each adapter: pricing and the Claude Code JSONL usage source
now, the OAuth limits source in its own task. Applications call
:func:`register` at startup to enable the provider.
"""

from __future__ import annotations

from tokemetry_core.normalization import ANTHROPIC_DESCRIPTOR
from tokemetry_core.pricing.anthropic import AnthropicPricingStrategy
from tokemetry_core.providers.claude_code import ClaudeCodeJsonlSource
from tokemetry_core.registry import ProviderRegistry


def register(registry: ProviderRegistry) -> None:
    """Register all available Anthropic adapters on ``registry``."""
    registry.register_provider(ANTHROPIC_DESCRIPTOR)
    registry.register_pricing(AnthropicPricingStrategy())
    registry.register_usage_source(ClaudeCodeJsonlSource.provider, ClaudeCodeJsonlSource)
