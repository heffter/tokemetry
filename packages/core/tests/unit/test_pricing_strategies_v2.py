"""Unit tests for the v2 provider pricing strategy plugins and their registry.

Each strategy is a pure declaration of a provider's billed token units plus its
reasoning rule, producing a ``unit_type`` -> quantity map. These tests use
provider-realistic counter fixtures (Anthropic with both cache TTL writes;
OpenAI with cached input, reasoning, and web search; Z.ai with cached input) and
prove the abstraction with a fake third-party strategy, plus registry resolution
and the generic fallback.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tokemetry_core.interfaces import ProviderPricingStrategyV2, ReasoningBilling
from tokemetry_core.pricing.strategies.anthropic import AnthropicPricingStrategyV2
from tokemetry_core.pricing.strategies.defaults import (
    default_v2_pricing_registry,
    register_pricing_strategies_v2,
)
from tokemetry_core.pricing.strategies.generic import GenericPricingStrategyV2
from tokemetry_core.pricing.strategies.openai import OpenAIPricingStrategyV2
from tokemetry_core.pricing.strategies.zai import ZaiPricingStrategyV2
from tokemetry_core.registry import ProviderRegistry, UnknownProviderError

#: A full five-counter token map, reused across strategy fixtures.
_TOKENS = {
    "input_token": 10,
    "output_token": 20,
    "cache_read_token": 5,
    "cache_write_short_token": 3,
    "cache_write_long_token": 2,
}


def test_anthropic_emits_five_units_and_folds_reasoning() -> None:
    strategy = AnthropicPricingStrategyV2()
    assert strategy.provider == "anthropic"
    assert strategy.reasoning_billing is ReasoningBilling.FOLD_INTO_OUTPUT
    assert strategy.emitted_token_units() == frozenset(_TOKENS)
    # FOLD_INTO_OUTPUT never needs the reasoning rate, even with reasoning tokens.
    assert strategy.needs_reasoning_rate(7) is False

    # Reasoning folds into output even when a reasoning rate is available.
    quantities = strategy.quantities(_TOKENS, 7, {}, reasoning_rate_available=True)
    assert quantities == {
        "input_token": Decimal(10),
        "output_token": Decimal(27),  # 20 + 7 reasoning folded in
        "cache_read_token": Decimal(5),
        "cache_write_short_token": Decimal(3),
        "cache_write_long_token": Decimal(2),
    }


def test_openai_drops_cache_writes_and_prices_reasoning_conditionally() -> None:
    strategy = OpenAIPricingStrategyV2()
    assert strategy.provider == "openai"
    assert strategy.reasoning_billing is ReasoningBilling.SEPARATE_IF_RATED
    assert strategy.emitted_token_units() == frozenset(
        {"input_token", "output_token", "cache_read_token"}
    )
    assert strategy.needs_reasoning_rate(7) is True
    assert strategy.needs_reasoning_rate(0) is False

    # No reasoning rate: reasoning folds into output, cache-write tiers dropped,
    # hosted-tool fee added.
    folded = strategy.quantities(
        _TOKENS, 7, {"web_search_request": 2.0}, reasoning_rate_available=False
    )
    assert folded == {
        "input_token": Decimal(10),
        "output_token": Decimal(27),  # 20 + 7 folded
        "cache_read_token": Decimal(5),
        "web_search_request": Decimal("2.0"),
    }

    # A reasoning rate exists: reasoning is a separate unit, output unchanged.
    separate = strategy.quantities(_TOKENS, 7, {}, reasoning_rate_available=True)
    assert separate["output_token"] == Decimal(20)
    assert separate["reasoning_token"] == Decimal(7)
    assert "cache_write_short_token" not in separate


def test_zai_emits_cached_input_without_cache_write_tiers() -> None:
    strategy = ZaiPricingStrategyV2()
    assert strategy.provider == "zai"
    assert strategy.reasoning_billing is ReasoningBilling.SEPARATE_IF_RATED
    assert strategy.emitted_token_units() == frozenset(
        {"input_token", "output_token", "cache_read_token"}
    )
    quantities = strategy.quantities(_TOKENS, 0, {}, reasoning_rate_available=False)
    assert set(quantities) == {"input_token", "output_token", "cache_read_token"}


def test_generic_is_token_linear_over_five_units() -> None:
    strategy = GenericPricingStrategyV2()
    assert strategy.provider == "generic"
    assert strategy.emitted_token_units() == frozenset(_TOKENS)
    quantities = strategy.quantities(_TOKENS, 0, {}, reasoning_rate_available=False)
    assert quantities == {unit: Decimal(count) for unit, count in _TOKENS.items()}


def test_registry_resolves_each_provider_and_falls_back_to_generic() -> None:
    registry = ProviderRegistry()
    register_pricing_strategies_v2(registry)

    assert isinstance(registry.pricing_v2("anthropic"), AnthropicPricingStrategyV2)
    assert isinstance(registry.pricing_v2("openai"), OpenAIPricingStrategyV2)
    assert isinstance(registry.pricing_v2("zai"), ZaiPricingStrategyV2)
    # An unregistered provider resolves to the generic fallback (NFR-MAIN-002).
    assert isinstance(registry.pricing_v2("mistral"), GenericPricingStrategyV2)
    assert registry.pricing_v2_providers() == ["anthropic", "generic", "openai", "zai"]


def test_pricing_v2_without_default_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(UnknownProviderError):
        registry.pricing_v2("anthropic")


def test_default_registry_is_memoized() -> None:
    assert default_v2_pricing_registry() is default_v2_pricing_registry()


def test_fake_provider_plugin_proves_the_abstraction() -> None:
    # A third-party plugin needs no core change: declare emitted units + rule.
    class FakeFoldingStrategy(ProviderPricingStrategyV2):
        provider = "myfake"
        reasoning_billing = ReasoningBilling.FOLD_INTO_OUTPUT

        def emitted_token_units(self) -> frozenset[str]:
            return frozenset({"input_token", "output_token"})

    registry = ProviderRegistry()
    register_pricing_strategies_v2(registry)
    registry.register_pricing_v2(FakeFoldingStrategy())

    strategy = registry.pricing_v2("myfake")
    quantities = strategy.quantities(_TOKENS, 4, {}, reasoning_rate_available=True)
    # Only the declared units survive; reasoning folds despite an available rate.
    assert quantities == {"input_token": Decimal(10), "output_token": Decimal(24)}
