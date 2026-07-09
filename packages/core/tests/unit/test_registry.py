"""Unit tests for the provider registry."""

import pytest
from tokemetry_core.pricing.anthropic import AnthropicPricingStrategy
from tokemetry_core.providers.anthropic import register as register_anthropic
from tokemetry_core.providers.fake import (
    FAKE_PROVIDER,
    FakePricingStrategy,
    FakeUsageSource,
    register,
)
from tokemetry_core.registry import ProviderRegistry, UnknownProviderError


def test_register_and_resolve_all_kinds() -> None:
    registry = ProviderRegistry()
    register(registry)

    assert isinstance(registry.usage_source(FAKE_PROVIDER), FakeUsageSource)
    assert registry.limits_source(FAKE_PROVIDER).provider == FAKE_PROVIDER
    assert isinstance(registry.pricing(FAKE_PROVIDER), FakePricingStrategy)


def test_usage_source_factory_returns_fresh_instances() -> None:
    registry = ProviderRegistry()
    register(registry)

    first = registry.usage_source(FAKE_PROVIDER)
    second = registry.usage_source(FAKE_PROVIDER)
    assert first is not second


def test_unknown_provider_raises() -> None:
    registry = ProviderRegistry()

    with pytest.raises(UnknownProviderError):
        registry.usage_source("nope")
    with pytest.raises(UnknownProviderError):
        registry.limits_source("nope")
    with pytest.raises(UnknownProviderError):
        registry.pricing("nope")


def test_anthropic_registration_provides_pricing_and_usage_source() -> None:
    registry = ProviderRegistry()
    register_anthropic(registry)

    assert isinstance(registry.pricing("anthropic"), AnthropicPricingStrategy)
    assert registry.usage_providers() == ["anthropic"]
    assert registry.usage_source("anthropic").provider == "anthropic"


def test_provider_listings() -> None:
    registry = ProviderRegistry()
    assert registry.usage_providers() == []

    register(registry)
    assert registry.usage_providers() == [FAKE_PROVIDER]
    assert registry.limits_providers() == [FAKE_PROVIDER]
    assert registry.pricing_providers() == [FAKE_PROVIDER]
