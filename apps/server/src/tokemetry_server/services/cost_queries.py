"""Dual cost metrics over the v2 ``computed_costs`` ledger (D-007, FR-COST-012).

Actual API spend and subscription-equivalent value are reported as two separate
sums and are *never* added together: ``actual_spend_usd`` sums the ``amount`` of
active ``api_billed`` rows (real out-of-pocket spend), while
``subscription_value_usd`` sums the ``subscription_equivalent_amount`` of active
``subscription`` rows (imputed value at equivalent API rates, no real spend).
:class:`DualCostMetrics` deliberately exposes no combined total, so a caller
cannot merge the two by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from tokemetry_server.db import models
from tokemetry_server.services.billing_mode import API_BILLED, SUBSCRIPTION


@dataclass(frozen=True)
class DualCostMetrics:
    """Actual API spend and subscription-equivalent value, never merged."""

    actual_spend_usd: Decimal
    subscription_value_usd: Decimal


async def _cost_sum(
    session: AsyncSession,
    amount_column: InstrumentedAttribute[Any],
    billing_mode: str,
    start: datetime | None,
    end: datetime | None,
) -> Decimal:
    """Sum ``amount_column`` over active cost rows of one billing mode.

    Optionally bounded by the event's ``ts_started`` in ``[start, end]``.
    """
    cost = models.ComputedCost
    statement = select(func.coalesce(func.sum(amount_column), 0)).where(
        cost.active.is_(True),
        cost.billing_mode == billing_mode,
    )
    if start is not None or end is not None:
        event = models.UsageEventV2
        statement = statement.join(
            event,
            (cost.provider == event.provider) & (cost.event_id == event.event_id),
        )
        if start is not None:
            statement = statement.where(event.ts_started >= start)
        if end is not None:
            statement = statement.where(event.ts_started <= end)
    value = await session.scalar(statement)
    return Decimal(str(value)) if value is not None else Decimal("0")


async def dual_cost_metrics(
    session: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> DualCostMetrics:
    """Return actual spend and subscription value as separate sums.

    Args:
        session: The async session to query.
        start: Inclusive lower bound on the event's ``ts_started`` (optional).
        end: Inclusive upper bound on the event's ``ts_started`` (optional).
    """
    actual = await _cost_sum(
        session, models.ComputedCost.amount, API_BILLED, start, end
    )
    subscription = await _cost_sum(
        session,
        models.ComputedCost.subscription_equivalent_amount,
        SUBSCRIPTION,
        start,
        end,
    )
    return DualCostMetrics(
        actual_spend_usd=actual, subscription_value_usd=subscription
    )
