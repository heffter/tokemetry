"""Provider registry.

Maps provider names to their interface implementations so collectors and
the server can resolve adapters from configuration strings. Registration is
explicit (no import-time magic): each provider package exposes a
``register(registry)`` function that the application calls at startup.
"""

from __future__ import annotations

from collections.abc import Callable

from tokemetry_core.interfaces import LimitsSource, PricingStrategy, UsageSource


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

    def register_usage_source(self, provider: str, factory: Callable[[], UsageSource]) -> None:
        """Register a factory producing the provider's usage source."""
        self._usage_sources[provider] = factory

    def register_limits_source(self, provider: str, factory: Callable[[], LimitsSource]) -> None:
        """Register a factory producing the provider's limits source."""
        self._limits_sources[provider] = factory

    def register_pricing(self, strategy: PricingStrategy) -> None:
        """Register a pricing strategy under its own provider name."""
        self._pricing[strategy.provider] = strategy

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

    def usage_providers(self) -> list[str]:
        """Provider names that have a registered usage source."""
        return sorted(self._usage_sources)

    def limits_providers(self) -> list[str]:
        """Provider names that have a registered limits source."""
        return sorted(self._limits_sources)

    def pricing_providers(self) -> list[str]:
        """Provider names that have a registered pricing strategy."""
        return sorted(self._pricing)
