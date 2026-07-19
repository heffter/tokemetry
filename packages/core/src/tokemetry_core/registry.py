"""Provider registry.

Maps provider names to their interface implementations so collectors and
the server can resolve adapters from configuration strings. Registration is
explicit (no import-time magic): each provider package exposes a
``register(registry)`` function that the application calls at startup.
"""

from __future__ import annotations

from collections.abc import Callable

from tokemetry_core.interfaces import (
    LimitsSource,
    PricingStrategy,
    ProviderPricingStrategyV2,
    UsageSource,
)
from tokemetry_core.models import ProviderDescriptor
from tokemetry_core.normalization import normalize_provider


class UnknownProviderError(KeyError):
    """Requested provider has no registered implementation of that kind."""


class ProviderRegistry:
    """Registry of provider adapter factories keyed by provider name.

    Factories (not instances) are registered for sources so each resolve
    call yields a fresh, independently configured object; pricing
    strategies are stateless and registered as instances.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._usage_sources: dict[str, Callable[[], UsageSource]] = {}
        self._limits_sources: dict[str, Callable[[], LimitsSource]] = {}
        self._pricing: dict[str, PricingStrategy] = {}
        self._pricing_v2: dict[str, ProviderPricingStrategyV2] = {}
        self._default_pricing_v2: ProviderPricingStrategyV2 | None = None
        self._descriptors: dict[str, ProviderDescriptor] = {}

    def register_provider(self, descriptor: ProviderDescriptor) -> None:
        """Register a provider descriptor under its canonical id."""
        self._descriptors[descriptor.id] = descriptor

    def register_usage_source(self, provider: str, factory: Callable[[], UsageSource]) -> None:
        """Register a factory producing the provider's usage source."""
        self._usage_sources[provider] = factory

    def register_limits_source(self, provider: str, factory: Callable[[], LimitsSource]) -> None:
        """Register a factory producing the provider's limits source."""
        self._limits_sources[provider] = factory

    def register_pricing(self, strategy: PricingStrategy) -> None:
        """Register a pricing strategy under its own provider name."""
        self._pricing[strategy.provider] = strategy

    def register_pricing_v2(
        self, strategy: ProviderPricingStrategyV2, *, default: bool = False
    ) -> None:
        """Register a v2 pricing strategy under its own provider name.

        Args:
            strategy: The provider strategy (stateless, registered as an instance).
            default: When true, this strategy is also the fallback returned by
                :meth:`pricing_v2` for any unregistered provider (the generic
                token-linear strategy, NFR-MAIN-002).
        """
        self._pricing_v2[strategy.provider] = strategy
        if default:
            self._default_pricing_v2 = strategy

    def usage_source(self, provider: str) -> UsageSource:
        """Instantiate the usage source registered for ``provider``.

        Raises:
            UnknownProviderError: If no usage source is registered.
        """
        try:
            return self._usage_sources[provider]()
        except KeyError:
            raise UnknownProviderError(provider) from None

    def limits_source(self, provider: str) -> LimitsSource:
        """Instantiate the limits source registered for ``provider``.

        Raises:
            UnknownProviderError: If no limits source is registered.
        """
        try:
            return self._limits_sources[provider]()
        except KeyError:
            raise UnknownProviderError(provider) from None

    def pricing(self, provider: str) -> PricingStrategy:
        """Return the pricing strategy registered for ``provider``.

        Raises:
            UnknownProviderError: If no strategy is registered.
        """
        try:
            return self._pricing[provider]
        except KeyError:
            raise UnknownProviderError(provider) from None

    def pricing_v2(self, provider: str) -> ProviderPricingStrategyV2:
        """Return the v2 pricing strategy for ``provider``.

        Falls back to the registered default (generic) strategy for an
        unregistered provider, so pricing never fails for lack of a plugin.

        Raises:
            UnknownProviderError: If no strategy is registered and no default
                has been set.
        """
        strategy = self._pricing_v2.get(provider)
        if strategy is not None:
            return strategy
        if self._default_pricing_v2 is not None:
            return self._default_pricing_v2
        raise UnknownProviderError(provider)

    def provider(self, provider_id: str) -> ProviderDescriptor:
        """Return the descriptor registered under the canonical ``provider_id``.

        Raises:
            UnknownProviderError: If no descriptor is registered.
        """
        try:
            return self._descriptors[provider_id]
        except KeyError:
            raise UnknownProviderError(provider_id) from None

    def is_provider_registered(self, provider_id: str) -> bool:
        """Whether a descriptor is registered under the canonical id."""
        return provider_id in self._descriptors

    def resolve_provider(self, raw: str) -> ProviderDescriptor | None:
        """Normalize ``raw`` and return its descriptor, or None if unregistered.

        An unregistered provider is not an error here (FR-PROVIDER-005): the
        caller decides how to handle it (ingest may still accept and mark it).
        """
        return self._descriptors.get(normalize_provider(raw))

    def providers(self) -> list[str]:
        """Canonical ids of all providers with a registered descriptor."""
        return sorted(self._descriptors)

    def usage_providers(self) -> list[str]:
        """Provider names that have a registered usage source."""
        return sorted(self._usage_sources)

    def limits_providers(self) -> list[str]:
        """Provider names that have a registered limits source."""
        return sorted(self._limits_sources)

    def pricing_providers(self) -> list[str]:
        """Provider names that have a registered pricing strategy."""
        return sorted(self._pricing)

    def pricing_v2_providers(self) -> list[str]:
        """Provider names that have a registered v2 pricing strategy."""
        return sorted(self._pricing_v2)
