"""Cost engine: turn usage events into USD using the pricing table.

The engine holds an in-memory :class:`PricingTable` snapshot (loaded at
startup) and resolves each event's price by ``(provider, model, date)``,
delegating the arithmetic to the provider's ``PricingStrategy``. Events for
models or providers with no known price return ``None`` (stored as a null
cost) and are recorded so the alerting layer can surface pricing gaps.
"""

from __future__ import annotations

from decimal import Decimal

from loguru import logger
from tokemetry_core.models import UsageEvent
from tokemetry_core.pricing.table import PricingTable, UnknownModelError
from tokemetry_core.registry import ProviderRegistry, UnknownProviderError


class CostEngine:
    """Computes event cost from a pricing table and provider strategies."""

    def __init__(self, table: PricingTable, registry: ProviderRegistry) -> None:
        """Create the engine.

        Args:
            table: Loaded price rows to resolve against.
            registry: Registry providing per-provider pricing strategies.
        """
        self._table = table
        self._registry = registry
        self._unknown_models: set[tuple[str, str]] = set()

    @property
    def unknown_models(self) -> frozenset[tuple[str, str]]:
        """(provider, model) pairs seen without a known price."""
        return frozenset(self._unknown_models)

    def cost(self, event: UsageEvent) -> Decimal | None:
        """Return the USD cost of ``event``, or None if no price is known."""
        try:
            price = self._table.resolve(event.provider, event.native_model, event.ts.date())
            strategy = self._registry.pricing(event.provider)
        except (UnknownModelError, UnknownProviderError):
            if (event.provider, event.native_model) not in self._unknown_models:
                logger.warning(
                    "no price for {}/{}; storing null cost",
                    event.provider,
                    event.native_model,
                )
            self._unknown_models.add((event.provider, event.native_model))
            return None
        return strategy.cost(event, price)
