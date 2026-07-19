"""Abstract provider interfaces.

All provider-specific knowledge in tokemetry lives behind these three
interfaces. Collectors drive ``UsageSource`` and ``LimitsSource``
implementations; the server resolves a ``PricingStrategy`` per provider.
Adding a provider means implementing these classes and registering them --
no core, server, or dashboard changes.
"""

from __future__ import annotations

import abc
import enum
from collections.abc import Mapping
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


class ReasoningBilling(enum.Enum):
    """How a provider bills reasoning ("thinking") tokens (FR-PRICE-011).

    ``FOLD_INTO_OUTPUT`` -- the provider never bills reasoning as a separate
    line (Anthropic); the tokens are always priced as output.
    ``SEPARATE_IF_RATED`` -- reasoning is priced at a ``reasoning_token`` rate
    when one is configured, otherwise folded into output (OpenAI, Z.ai).
    """

    FOLD_INTO_OUTPUT = "fold_into_output"
    SEPARATE_IF_RATED = "separate_if_rated"


class ProviderPricingStrategyV2(abc.ABC):
    """Declares a provider's provider-neutral v2 unit-pricing semantics (D-006).

    The v2 cost path splits cleanly: rate resolution, precedence, summation, and
    cost status are provider-neutral and live in the server cost engine, while
    the provider-specific part -- which token unit types the provider actually
    bills, and how reasoning tokens are handled -- lives here. A strategy maps an
    event's counters to priceable ``unit_type`` quantities; the engine then
    resolves each unit through the rate cards. New providers are therefore
    plugins: implement this and register it, with no core or engine changes
    (PP-011, NFR-MAIN-002).

    Counters outside :meth:`emitted_token_units` are ignored, so a provider with
    no cache-write TTL tiers (OpenAI) never has a single cache-write count
    misrepresented as an Anthropic 5m/1h category (FR-DIM-006). Reasoning is
    governed by :attr:`reasoning_billing`, independent of the emitted set.
    """

    #: Provider id this strategy prices (the registry key), e.g. ``"anthropic"``.
    provider: str

    #: How this provider bills reasoning tokens.
    reasoning_billing: ReasoningBilling = ReasoningBilling.SEPARATE_IF_RATED

    @abc.abstractmethod
    def emitted_token_units(self) -> frozenset[str]:
        """The token unit types this provider bills (subset of TOKEN_UNIT_TYPES).

        Excludes ``reasoning_token`` (governed by :attr:`reasoning_billing`).
        """

    def needs_reasoning_rate(self, reasoning_tokens: int) -> bool:
        """Whether the engine must look up a ``reasoning_token`` rate.

        Only ``SEPARATE_IF_RATED`` providers with reasoning tokens need the
        lookup; ``FOLD_INTO_OUTPUT`` providers never do, saving a query.
        """
        return (
            reasoning_tokens > 0
            and self.reasoning_billing is ReasoningBilling.SEPARATE_IF_RATED
        )

    def quantities(
        self,
        token_counts: Mapping[str, int],
        reasoning_tokens: int,
        billable_units: Mapping[str, float],
        *,
        reasoning_rate_available: bool,
    ) -> dict[str, Decimal]:
        """Return the priceable ``unit_type`` -> quantity map for one event.

        Maps the emitted token counters to ``Decimal`` quantities, applies the
        provider's reasoning rule, and adds the non-token billable units. Zero
        quantities are retained; the engine filters them before rate resolution.

        Args:
            token_counts: ``unit_type`` -> count for the event's token counters.
            reasoning_tokens: The event's reasoning-token count.
            billable_units: Non-token ``unit_type`` -> quantity (hosted-tool
                fees, media, storage).
            reasoning_rate_available: Whether a ``reasoning_token`` rate exists
                for this model at the event's timestamp (the engine's lookup).
        """
        emitted = self.emitted_token_units()
        quantities: dict[str, Decimal] = {
            unit_type: Decimal(count)
            for unit_type, count in token_counts.items()
            if unit_type in emitted
        }
        if reasoning_tokens > 0:
            separate = (
                self.reasoning_billing is ReasoningBilling.SEPARATE_IF_RATED
                and reasoning_rate_available
            )
            fold_target = "reasoning_token" if separate else "output_token"
            quantities[fold_target] = quantities.get(fold_target, Decimal(0)) + Decimal(
                reasoning_tokens
            )
        for unit_type, quantity in billable_units.items():
            quantities[unit_type] = quantities.get(unit_type, Decimal(0)) + Decimal(
                str(quantity)
            )
        return quantities
