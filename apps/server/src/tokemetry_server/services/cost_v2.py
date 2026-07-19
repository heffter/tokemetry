"""Cost engine v2: price a final attempt event from rate cards (TOK-5).

For a ``final`` ``attempt`` event (snapshots are never priced, FR-COST-001) the
engine gathers the consumed unit quantities -- the six token counters mapped to
their unit types plus the non-token ``billable_units`` -- resolves each through
the rate service, sums the ``Decimal`` amounts quantized to a micro-unit, and
records a ``computed_costs`` row stamped with the current pricing-state version.

Status semantics: every consumed unit priced -> ``priced``; some priced and some
without a rate -> ``partial`` with ``missing_units`` (FR-PRICE-002); nothing
priced -> ``unpriced`` with a null amount (PP-008); a resolver error -> ``error``
(never an ingest rejection, FR-COST-008). Reasoning tokens are priced at the
``reasoning_token`` rate when one exists, otherwise folded into output
(FR-PRICE default strategy); hosted-tool and search fees are additive to token
fees (FR-PRICE-013). Cost never rejects or blocks ingest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.pricing.strategies.defaults import default_v2_pricing_registry
from tokemetry_core.registry import ProviderRegistry
from tokemetry_core.usage_v2 import UsageEventV2

from tokemetry_server.db import models
from tokemetry_server.services.billing_mode import SUBSCRIPTION, resolve_billing_mode
from tokemetry_server.services.computed_costs import record_cost
from tokemetry_server.services.pricing_v2 import current_pricing_version, resolve_rate

#: Quantum for summed cost amounts: one micro-unit of currency.
_QUANTUM = Decimal("0.000001")

#: Event token counter attribute -> its unit type.
_TOKEN_UNITS: dict[str, str] = {
    "input_tokens": "input_token",
    "output_tokens": "output_token",
    "cache_read_tokens": "cache_read_token",
    "cache_write_short_tokens": "cache_write_short_token",
    "cache_write_long_tokens": "cache_write_long_token",
}


@dataclass
class CostResult:
    """The computed cost of one event before it is recorded."""

    amount: Decimal | None
    cost_status: str
    missing_units: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PriceInputs:
    """The fields needed to price one event, from a wire event or a ledger row."""

    provider: str
    native_model: str
    at: datetime
    service_tier: str | None
    token_counts: dict[str, int]
    reasoning_tokens: int
    billable_units: dict[str, float]
    source_id: int | None
    machine: str | None


class CostEngineV2:
    """Prices final attempt events against the rate cards."""

    def __init__(
        self,
        session: AsyncSession,
        registry: ProviderRegistry | None = None,
        billing_mode_overrides: Mapping[str, str] | None = None,
    ) -> None:
        """Create the engine bound to the caller's transaction.

        Args:
            session: The async session/transaction to price within.
            registry: Provider registry supplying the v2 pricing strategies;
                defaults to the built-in strategies (anthropic/openai/zai plus
                a generic fallback) when omitted.
            billing_mode_overrides: Account-level machine -> billing_mode
                overrides (D-007); empty by default.
        """
        self._session = session
        self._registry = registry if registry is not None else default_v2_pricing_registry()
        self._billing_mode_overrides: Mapping[str, str] = billing_mode_overrides or {}

    async def compute_and_record(
        self, event: UsageEventV2, pricing_version: str | None = None
    ) -> models.ComputedCost | None:
        """Price a v2 wire ``event`` and record its cost; None if not final."""
        if str(event.event_kind) != "attempt" or str(event.finality) != "final":
            return None
        inputs = PriceInputs(
            provider=event.provider,
            native_model=event.native_model,
            at=event.ts_started,
            service_tier=event.service_tier,
            token_counts={
                unit_type: getattr(event, counter)
                for counter, unit_type in _TOKEN_UNITS.items()
            },
            reasoning_tokens=event.reasoning_tokens,
            billable_units=dict(event.billable_units or {}),
            source_id=None,  # the wire event is not yet resolved to a source row
            machine=event.machine,
        )
        return await self._record(event.provider, event.event_id, inputs, pricing_version)

    async def compute_and_record_row(
        self,
        row: models.UsageEventV2,
        billable_units: dict[str, float],
        pricing_version: str | None = None,
    ) -> models.ComputedCost | None:
        """Price a ledger row (the async worker path); None if not final."""
        if row.event_kind != "attempt" or row.finality != "final":
            return None
        started = row.ts_started
        at = started if started.tzinfo is not None else started.replace(tzinfo=UTC)
        inputs = PriceInputs(
            provider=row.provider,
            native_model=row.native_model,
            at=at,
            service_tier=row.service_tier,
            token_counts={
                unit_type: getattr(row, counter)
                for counter, unit_type in _TOKEN_UNITS.items()
            },
            reasoning_tokens=row.reasoning_tokens,
            billable_units=billable_units,
            source_id=row.source_id,
            machine=row.machine,
        )
        return await self._record(row.provider, row.event_id, inputs, pricing_version)

    async def _record(
        self,
        provider: str,
        event_id: str,
        inputs: PriceInputs,
        pricing_version: str | None,
    ) -> models.ComputedCost:
        """Compute and record the cost for one event."""
        version = (
            pricing_version
            if pricing_version is not None
            else await current_pricing_version(self._session)
        )
        result = await self._compute(inputs)
        billing_mode = resolve_billing_mode(
            await self._source_billing_mode(inputs.source_id),
            inputs.machine,
            self._billing_mode_overrides,
        )
        # Subscription usage carries no real spend: the computed amount is the
        # subscription-equivalent value and ``amount`` stays null (FR-COST-011).
        subscription_equivalent = result.amount if billing_mode == SUBSCRIPTION else None
        amount = None if billing_mode == SUBSCRIPTION else result.amount
        return await record_cost(
            self._session,
            provider,
            event_id,
            amount=amount,
            cost_status=result.cost_status,
            pricing_version=version,
            billing_mode=billing_mode,
            subscription_equivalent_amount=subscription_equivalent,
            missing_units={"units": result.missing_units} if result.missing_units else None,
        )

    async def _source_billing_mode(self, source_id: int | None) -> str | None:
        """Return the ``billing_mode`` of the event's source, or None if absent."""
        if source_id is None:
            return None
        mode: str | None = await self._session.scalar(
            select(models.Source.billing_mode).where(models.Source.id == source_id)
        )
        return mode

    async def _compute(self, inputs: PriceInputs) -> CostResult:
        """Compute the cost of a final attempt (status + amount)."""
        quantities = await self._quantities(inputs)
        consumed = {unit: qty for unit, qty in quantities.items() if qty > 0}

        if not consumed:
            return await self._zero_consumption_result(inputs)

        total = Decimal(0)
        missing: list[str] = []
        priced_any = False
        for unit_type, quantity in consumed.items():
            try:
                rate = await resolve_rate(
                    self._session, inputs.provider, inputs.native_model, unit_type,
                    inputs.at, tier=inputs.service_tier,
                )
            except Exception as exc:  # resolver failure -> error status, never reject
                logger.warning(
                    "cost resolution error for {} {}: {}",
                    inputs.native_model, unit_type, exc,
                )
                return CostResult(amount=None, cost_status="error")
            if rate is None:
                missing.append(unit_type)
            else:
                total += rate.unit_price * quantity
                priced_any = True

        if not priced_any:
            return CostResult(amount=None, cost_status="unpriced", missing_units=missing)
        if missing:
            return CostResult(
                amount=total.quantize(_QUANTUM), cost_status="partial", missing_units=missing
            )
        return CostResult(amount=total.quantize(_QUANTUM), cost_status="priced")

    async def _quantities(self, inputs: PriceInputs) -> dict[str, Decimal]:
        """Gather priceable unit quantities via the provider's pricing strategy.

        The strategy (resolved by provider, generic fallback) declares the
        emitted token units and the reasoning rule; the engine supplies whether
        a ``reasoning_token`` rate exists so a ``separate_if_rated`` provider can
        price reasoning distinctly, and stays provider-neutral otherwise.
        """
        strategy = self._registry.pricing_v2(inputs.provider)
        reasoning_rate_available = False
        if strategy.needs_reasoning_rate(inputs.reasoning_tokens):
            reasoning_rate_available = (
                await resolve_rate(
                    self._session, inputs.provider, inputs.native_model,
                    "reasoning_token", inputs.at, tier=inputs.service_tier,
                )
                is not None
            )
        return strategy.quantities(
            inputs.token_counts,
            inputs.reasoning_tokens,
            inputs.billable_units,
            reasoning_rate_available=reasoning_rate_available,
        )

    async def _zero_consumption_result(self, inputs: PriceInputs) -> CostResult:
        """Price a zero-usage attempt: priced=0 if the model is known, else unpriced."""
        try:
            rate = await resolve_rate(
                self._session, inputs.provider, inputs.native_model, "input_token",
                inputs.at, tier=inputs.service_tier,
            )
        except Exception as exc:
            logger.warning("cost resolution error for {}: {}", inputs.native_model, exc)
            return CostResult(amount=None, cost_status="error")
        if rate is None:
            return CostResult(amount=None, cost_status="unpriced")
        return CostResult(amount=Decimal(0).quantize(_QUANTUM), cost_status="priced")
