"""Abstract provider interfaces.

All provider-specific knowledge in tokemetry lives behind these three
interfaces. Collectors drive ``UsageSource`` and ``LimitsSource``
implementations; the server resolves a ``PricingStrategy`` per provider.
Adding a provider means implementing these classes and registering them --
no core, server, or dashboard changes.
"""

from __future__ import annotations

import abc
from decimal import Decimal

from tokemetry_core.models import (
    DailyAggregate,
    LimitSnapshot,
    ParseResult,
    PriceRow,
    SourceFile,
    UsageEvent,
)


class UsageSource(abc.ABC):
    """Discovers and incrementally parses a provider's local usage artifacts.

    Implementations must be stateless between calls: the collector owns
    offset persistence and passes the resume position into :meth:`parse`.
    """

    #: Provider identifier this source emits events for (for example
    #: ``"anthropic"``). Used as the registry key and stored on every event.
    provider: str

    @abc.abstractmethod
    def discover(self) -> list[SourceFile]:
        """Return all currently existing artifacts this source can parse.

        Returns:
            Files with their current sizes; the collector compares sizes
            against persisted offsets to decide what needs parsing.
        """

    @abc.abstractmethod
    def parse(self, file: SourceFile, offset: int) -> ParseResult:
        """Parse ``file`` from byte ``offset`` and normalize new records.

        Args:
            file: A file previously returned by :meth:`discover`.
            offset: Byte position to resume from (0 for a fresh file).

        Returns:
            The normalized events found after ``offset`` plus the byte
            position the next call should resume from.

        Raises:
            OSError: If the file cannot be read.
        """

    @abc.abstractmethod
    def bootstrap(self) -> list[DailyAggregate]:
        """Return coarse historical aggregates for a one-time import.

        Sources without an aggregate cache return an empty list.
        """


class LimitsSource(abc.ABC):
    """Polls a provider for authoritative rate-limit utilization."""

    #: Provider identifier, matching the related :class:`UsageSource`.
    provider: str

    @abc.abstractmethod
    def poll(self) -> list[LimitSnapshot]:
        """Fetch the current utilization of every limit window.

        Returns:
            One snapshot per limit window the provider reports; an empty
            list when the provider reports nothing.

        Raises:
            LimitsUnavailableError: If the endpoint cannot be reached or
                refuses the request; callers degrade to local estimates.
        """


class LimitsUnavailableError(RuntimeError):
    """Raised by :meth:`LimitsSource.poll` when official limits cannot be read.

    Deliberately a distinct type so collectors can degrade gracefully
    (fall back to local estimates) without masking programming errors.
    """


class PricingStrategy(abc.ABC):
    """Computes the USD cost of a usage event from a per-MTok price row.

    Strategies encode provider-specific billing rules (for example
    Anthropic's cache write/read multipliers are already baked into the
    price row columns, while other providers may need conditional logic).
    """

    #: Provider identifier this strategy prices events for.
    provider: str

    @abc.abstractmethod
    def cost(self, event: UsageEvent, price: PriceRow) -> Decimal:
        """Return the USD cost of ``event`` under ``price``.

        Args:
            event: A normalized usage event of this strategy's provider.
            price: The price row effective at the event's timestamp.

        Returns:
            Cost in USD, quantized by the implementation.
        """
