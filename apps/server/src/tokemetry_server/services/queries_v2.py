"""Provider-neutral v2 usage and cost aggregation (TOK-7, FR-QUERY-006..008).

Grouped read queries for the v2 API. Usage is aggregated from the ledger
(``usage_events_v2``, final attempts only) so it carries attempt counts and all
six token counters and never counts snapshots or logical-request summaries
(FR-QUERY-007/008). Cost is aggregated from the active ``computed_costs`` rows,
keeping actual API spend and subscription-equivalent value as separate series
(never merged, FR-COST-012/D-007), broken down by cost status, and stamped with
the pricing version (or ``mixed`` when a group spans several, FR-QUERY-006).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, case, cast, func, select
from sqlalchemy import Date as SQLDate
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.query_framework import QueryFilters

#: Grouping dimensions served from the ledger, dimension -> column.
_LEDGER_DIMENSIONS: dict[str, Any] = {
    "provider": models.UsageEventV2.provider,
    "model": models.UsageEventV2.native_model,
    "machine": func.coalesce(models.UsageEventV2.machine, ""),
    "project": func.coalesce(models.UsageEventV2.project, ""),
    "environment": func.coalesce(models.UsageEventV2.environment, ""),
    "session": func.coalesce(models.UsageEventV2.session_id, ""),
}

#: Every dimension the usage/cost endpoints accept.
USAGE_DIMENSIONS = frozenset({"day", "source", *_LEDGER_DIMENSIONS})


@dataclass(frozen=True)
class UsageRow:
    """One grouped usage aggregate (six counters plus attempt count)."""

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    reasoning_tokens: int
    total_tokens: int
    attempt_count: int


@dataclass(frozen=True)
class CostRow:
    """One grouped cost aggregate: dual metrics, status split, pricing version."""

    key: str
    actual_spend_usd: Decimal
    subscription_value_usd: Decimal
    cost_priced_usd: Decimal
    cost_partial_usd: Decimal
    cost_estimated_usd: Decimal
    unpriced_event_count: int
    pricing_version: str


@dataclass(frozen=True)
class ReconciliationRow:
    """Observed-versus-computed cost drift for one provider (FR-COST-003/005)."""

    provider: str
    computed_usd: Decimal
    observed_usd: Decimal
    drift_usd: Decimal
    event_count: int


def _usage_key(dimension: str) -> Any:
    """The group-by key column for a usage/cost dimension."""
    if dimension == "day":
        return cast(models.UsageEventV2.ts_started, SQLDate)
    if dimension == "source":
        return func.coalesce(models.Source.name, "")
    return _LEDGER_DIMENSIONS[dimension]


def _apply_ledger_filters(
    statement: Select[Any], filters: QueryFilters
) -> Select[Any]:
    """Apply the common dimension and pseudo filters to a ledger query."""
    event = models.UsageEventV2
    column_filters = {
        event.provider: filters.provider,
        event.native_model: filters.native_model,
        event.machine: filters.machine,
        event.project: filters.project,
        event.session_id: filters.session_id,
        event.environment: filters.environment,
    }
    for column, value in column_filters.items():
        if value is not None:
            statement = statement.where(column == value)
    if filters.outcome is not None:
        statement = statement.where(event.success.is_(filters.outcome == "success"))
    if filters.unknown_provider:
        registered = select(models.Provider.id).where(
            models.Provider.id == event.provider
        )
        statement = statement.where(~registered.exists())
    if filters.unknown_model:
        known = select(models.Model.native_model_id).where(
            models.Model.provider == event.provider,
            models.Model.native_model_id == event.native_model,
        )
        statement = statement.where(~known.exists())
    return statement


async def grouped_usage(
    session: AsyncSession,
    dimension: str,
    start: datetime,
    end: datetime,
    filters: QueryFilters,
) -> list[UsageRow]:
    """Aggregate final-attempt usage over a range grouped by one dimension."""
    event = models.UsageEventV2
    key = _usage_key(dimension)
    total = (
        event.input_tokens + event.output_tokens + event.cache_read_tokens
        + event.cache_write_short_tokens + event.cache_write_long_tokens
        + event.reasoning_tokens
    )
    statement = select(
        key.label("key"),
        func.coalesce(func.sum(event.input_tokens), 0),
        func.coalesce(func.sum(event.output_tokens), 0),
        func.coalesce(func.sum(event.cache_read_tokens), 0),
        func.coalesce(func.sum(event.cache_write_short_tokens), 0),
        func.coalesce(func.sum(event.cache_write_long_tokens), 0),
        func.coalesce(func.sum(event.reasoning_tokens), 0),
        func.coalesce(func.sum(total), 0),
        func.count(),
    ).select_from(event)
    if dimension == "source":
        statement = statement.join(
            models.Source, models.Source.id == event.source_id, isouter=True
        )
    statement = statement.where(
        event.event_kind == "attempt", event.finality == "final",
        event.ts_started >= start, event.ts_started <= end,
    )
    statement = _apply_ledger_filters(statement, filters)
    statement = statement.group_by(key).order_by(key.asc())

    rows = (await session.execute(statement)).all()
    return [
        UsageRow(
            key=_key_to_str(r[0]),
            input_tokens=int(r[1] or 0), output_tokens=int(r[2] or 0),
            cache_read_tokens=int(r[3] or 0), cache_write_short_tokens=int(r[4] or 0),
            cache_write_long_tokens=int(r[5] or 0), reasoning_tokens=int(r[6] or 0),
            total_tokens=int(r[7] or 0), attempt_count=int(r[8] or 0),
        )
        for r in rows
    ]


async def grouped_costs(
    session: AsyncSession,
    dimension: str,
    start: datetime,
    end: datetime,
    filters: QueryFilters,
) -> list[CostRow]:
    """Aggregate active computed costs over a range grouped by one dimension."""
    event = models.UsageEventV2
    cost = models.ComputedCost
    key = _usage_key(dimension)
    not_sub = cost.billing_mode != "subscription"

    def _sum_when(condition: ColumnElement[bool], value: Any) -> ColumnElement[Any]:
        return func.coalesce(func.sum(case((condition, value), else_=None)), 0)

    statement = (
        select(
            key.label("key"),
            _sum_when(not_sub, cost.amount),  # actual spend (all api_billed)
            _sum_when(cost.billing_mode == "subscription", cost.subscription_equivalent_amount),
            _sum_when(and_(not_sub, cost.cost_status == "priced"), cost.amount),
            _sum_when(and_(not_sub, cost.cost_status == "partial"), cost.amount),
            _sum_when(and_(not_sub, cost.cost_status == "estimated"), cost.amount),
            func.sum(case((cost.cost_status.in_(("unpriced", "error")), 1), else_=0)),
            func.min(cost.pricing_version),
            func.count(func.distinct(cost.pricing_version)),
        )
        .select_from(cost)
        .join(event, and_(cost.provider == event.provider, cost.event_id == event.event_id))
        .where(cost.active.is_(True), event.ts_started >= start, event.ts_started <= end)
    )
    if dimension == "source":
        statement = statement.join(
            models.Source, models.Source.id == event.source_id, isouter=True
        )
    statement = _apply_ledger_filters(statement, filters)
    statement = statement.group_by(key).order_by(key.asc())

    rows = (await session.execute(statement)).all()
    return [
        CostRow(
            key=_key_to_str(r[0]),
            actual_spend_usd=_dec(r[1]), subscription_value_usd=_dec(r[2]),
            cost_priced_usd=_dec(r[3]), cost_partial_usd=_dec(r[4]),
            cost_estimated_usd=_dec(r[5]), unpriced_event_count=int(r[6] or 0),
            pricing_version=str(r[7]) if int(r[8] or 0) <= 1 else "mixed",
        )
        for r in rows
    ]


async def cost_reconciliation(
    session: AsyncSession, start: datetime, end: datetime, filters: QueryFilters
) -> list[ReconciliationRow]:
    """Observed-versus-computed cost drift by provider, where observed is known."""
    event = models.UsageEventV2
    cost = models.ComputedCost
    statement = (
        select(
            event.provider,
            func.coalesce(func.sum(cost.amount), 0),
            func.coalesce(func.sum(cost.observed_cost), 0),
            func.count(),
        )
        .select_from(cost)
        .join(event, and_(cost.provider == event.provider, cost.event_id == event.event_id))
        .where(
            cost.active.is_(True), cost.observed_cost.isnot(None),
            event.ts_started >= start, event.ts_started <= end,
        )
    )
    statement = _apply_ledger_filters(statement, filters)
    statement = statement.group_by(event.provider).order_by(event.provider)

    rows = (await session.execute(statement)).all()
    return [
        ReconciliationRow(
            provider=str(r[0]), computed_usd=_dec(r[1]), observed_usd=_dec(r[2]),
            drift_usd=_dec(r[2]) - _dec(r[1]), event_count=int(r[3] or 0),
        )
        for r in rows
    ]


def _dec(value: object) -> Decimal:
    """Coerce a nullable DB numeric to a non-null Decimal (0 for None)."""
    return Decimal("0") if value is None else Decimal(str(value))


def _key_to_str(value: object) -> str:
    """Render a group-by key value as a string (dates as ISO)."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
