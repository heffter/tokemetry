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

from dataclasses import dataclass, field
from decimal import Decimal

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import UsageEventV2

from tokemetry_server.db import models
from tokemetry_server.services.computed_costs import record_cost
from tokemetry_server.services.pricing_v2 import current_pricing_version, resolve_rate

#: Quantum for summed cost amounts: one micro-unit of currency.
_QUANTUM = Decimal("0.000001")

#: Event token counter -> its unit type.
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


class CostEngineV2:
    """Prices final attempt events against the rate cards."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the engine bound to the caller's transaction."""
        self._session = session

    async def compute_and_record(
        self, event: UsageEventV2, pricing_version: str | None = None
    ) -> models.ComputedCost | None:
        """Price ``event`` and record a computed_costs row; return it or None.

        Returns ``None`` for events that are not final attempts (they are never
        priced). Never raises for a pricing failure -- the status carries it.
        """
        if str(event.event_kind) != "attempt" or str(event.finality) != "final":
            return None

        version = (
            pricing_version
            if pricing_version is not None
            else await current_pricing_version(self._session)
        )
        result = await self._compute(event)
        return await record_cost(
            self._session,
            event.provider,
            event.event_id,
            amount=result.amount,
            cost_status=result.cost_status,
            pricing_version=version,
            missing_units={"units": result.missing_units} if result.missing_units else None,
        )

    async def _compute(self, event: UsageEventV2) -> CostResult:
        """Compute the cost of a final attempt event (status + amount)."""
        quantities = await self._quantities(event)
        consumed = {unit: qty for unit, qty in quantities.items() if qty > 0}

        if not consumed:
            return await self._zero_consumption_result(event)

        total = Decimal(0)
        missing: list[str] = []
        priced_any = False
        for unit_type, quantity in consumed.items():
            try:
                rate = await resolve_rate(
                    self._session, event.provider, event.native_model, unit_type,
                    event.ts_started, tier=event.service_tier,
                )
            except Exception as exc:  # resolver failure -> error status, never reject
                logger.warning(
                    "cost resolution error for {}/{} {}: {}",
                    event.provider, event.event_id, unit_type, exc,
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

    async def _quantities(self, event: UsageEventV2) -> dict[str, Decimal]:
        """Gather unit quantities, folding reasoning and adding billable units."""
        quantities: dict[str, Decimal] = {
            unit_type: Decimal(getattr(event, counter))
            for counter, unit_type in _TOKEN_UNITS.items()
        }
        if event.reasoning_tokens > 0:
            reasoning_rate = await resolve_rate(
                self._session, event.provider, event.native_model, "reasoning_token",
                event.ts_started, tier=event.service_tier,
            )
            if reasoning_rate is not None:
                quantities["reasoning_token"] = Decimal(event.reasoning_tokens)
            else:  # fold reasoning into output when no separate rate exists
                quantities["output_token"] += Decimal(event.reasoning_tokens)
        if event.billable_units:
            for unit_type, quantity in event.billable_units.items():
                quantities[unit_type] = quantities.get(unit_type, Decimal(0)) + Decimal(
                    str(quantity)
                )
        return quantities

    async def _zero_consumption_result(self, event: UsageEventV2) -> CostResult:
        """Price a zero-usage attempt: priced=0 if the model is known, else unpriced."""
        try:
            rate = await resolve_rate(
                self._session, event.provider, event.native_model, "input_token",
                event.ts_started, tier=event.service_tier,
            )
        except Exception as exc:
            logger.warning("cost resolution error for {}: {}", event.event_id, exc)
            return CostResult(amount=None, cost_status="error")
        if rate is None:
            return CostResult(amount=None, cost_status="unpriced")
        return CostResult(amount=Decimal(0).quantize(_QUANTUM), cost_status="priced")
