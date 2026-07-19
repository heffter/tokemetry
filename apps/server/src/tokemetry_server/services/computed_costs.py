"""Computed-cost recording, kept separate from usage facts (TOK-5).

Cost lives in ``computed_costs``, never on the usage row. :func:`record_cost`
writes the authoritative cost for one event and enforces exactly one active row
per event (FR-COST-001): recording deactivates any prior active rows and makes
the recorded ``(provider, event_id, pricing_version)`` row active. Repricing
under a new pricing version keeps the prior version's row (inactive) for audit.
Observed (exporter-reported) cost is stored separately and never replaces the
authoritative computed cost (FR-COST-004); drift is a query concern
(FR-COST-005). Uniqueness is enforced in the service for SQLite compatibility.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models

#: Recognized cost statuses (FR-COST-006).
COST_STATUSES = frozenset(
    {"priced", "unpriced", "partial", "estimated", "error"}
)


async def record_cost(
    session: AsyncSession,
    provider: str,
    event_id: str,
    *,
    amount: Decimal | None,
    cost_status: str,
    pricing_version: str,
    billing_mode: str = "api_billed",
    subscription_equivalent_amount: Decimal | None = None,
    missing_units: dict[str, Any] | None = None,
    currency: str = "USD",
) -> models.ComputedCost:
    """Record the authoritative cost for an event; return the active row.

    Deactivates any other active row for the event, then upserts the
    ``(provider, event_id, pricing_version)`` row as active.

    Raises:
        ValueError: If ``cost_status`` is not a recognized status.
    """
    if cost_status not in COST_STATUSES:
        raise ValueError(f"unknown cost_status: {cost_status!r}")

    await session.execute(
        update(models.ComputedCost)
        .where(
            models.ComputedCost.provider == provider,
            models.ComputedCost.event_id == event_id,
            models.ComputedCost.active.is_(True),
        )
        .values(active=False)
    )

    existing = (
        await session.execute(
            select(models.ComputedCost).where(
                models.ComputedCost.provider == provider,
                models.ComputedCost.event_id == event_id,
                models.ComputedCost.pricing_version == pricing_version,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if existing is None:
        row = models.ComputedCost(
            provider=provider,
            event_id=event_id,
            pricing_version=pricing_version,
            cost_status=cost_status,
            amount=amount,
            currency=currency,
            billing_mode=billing_mode,
            subscription_equivalent_amount=subscription_equivalent_amount,
            missing_units=missing_units,
            observed_cost=None,
            calculated_at=now,
            active=True,
        )
        session.add(row)
        return row

    existing.cost_status = cost_status
    existing.amount = amount
    existing.currency = currency
    existing.billing_mode = billing_mode
    existing.subscription_equivalent_amount = subscription_equivalent_amount
    existing.missing_units = missing_units
    existing.calculated_at = now
    existing.active = True
    return existing


async def active_cost(
    session: AsyncSession, provider: str, event_id: str
) -> models.ComputedCost | None:
    """Return the active computed-cost row for an event, if any."""
    return (
        await session.execute(
            select(models.ComputedCost).where(
                models.ComputedCost.provider == provider,
                models.ComputedCost.event_id == event_id,
                models.ComputedCost.active.is_(True),
            )
        )
    ).scalar_one_or_none()
