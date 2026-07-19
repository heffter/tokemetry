"""Daily rollup refresh over the provider-neutral v2 ledger (TOK-7).

After events are ingested (or corrected, or repriced) the affected days'
rollups are recomputed from ``usage_events_v2`` -- only ``final`` ``attempt``
events (FR-ROLLUP-001), which the revision engine guarantees is a single active
row per event (never a superseded snapshot, FR-ROLLUP-002) and never a
``logical_request`` summary (FR-ROLLUP-003). Cost is taken from the active
``computed_costs`` row and split by status and billing mode (FR-ROLLUP-007); an
event not yet costed falls back to its transitional ``cost_usd`` so v1 datasets
roll up identically to the pre-migration algorithm. Recomputing whole days
(rather than deltas) is idempotent under keep-max updates (FR-ROLLUP-008): the
aggregate always reflects the current stored rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.projects import DEFAULT_ROOTS, project_group

from tokemetry_server.db import models
from tokemetry_server.db.upsert import daily_rollups_upsert

#: Provenance stamped on rollups computed from real events.
DERIVED = "derived"

#: Cost statuses that count an event as not (fully) priced.
_UNPRICED_STATUSES = ("unpriced", "error")

#: Rollup token columns, in the order the aggregate query selects them.
_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_short_tokens",
    "cache_write_long_tokens",
    "reasoning_tokens",
    "total_tokens",
)


@dataclass
class _Agg:
    """Accumulated token totals (``_TOKEN_KEYS`` order) and split cost for a group."""

    tokens: list[int]
    cost_priced: Decimal | None = None
    cost_partial: Decimal | None = None
    cost_estimated: Decimal | None = None
    unpriced_count: int = 0
    subscription_value: Decimal | None = None


async def refresh_rollups_for_days(
    session: AsyncSession,
    dialect_name: str,
    days: Iterable[date],
    roots: Sequence[str] = DEFAULT_ROOTS,
) -> int:
    """Recompute and upsert rollups for each given day; return rows written.

    Args:
        session: Active session (caller owns the transaction).
        dialect_name: Dialect for the upsert syntax.
        days: Days to recompute (typically the days touched by a batch,
            correction, or reprice).
        roots: Project root markers driving directory-to-project grouping.
    """
    written = 0
    for day in days:
        rows = await _aggregate_day(session, day, roots)
        if rows:
            stmt = daily_rollups_upsert(dialect_name, models.DailyRollup.__table__, rows)
            await session.execute(stmt)
            written += len(rows)
    return written


async def rebuild_all_rollups(
    session: AsyncSession,
    dialect_name: str,
    roots: Sequence[str] = DEFAULT_ROOTS,
) -> int:
    """Delete every derived rollup and rebuild it from events.

    Required after the project-grouping rule changes: rows keyed by the old
    (raw) project would otherwise linger alongside the newly grouped rows. A
    plain per-day upsert cannot remove them because the project key differs.
    """
    await session.execute(
        delete(models.DailyRollup).where(models.DailyRollup.provenance == DERIVED)
    )
    days = _event_days(await _span(session))
    return await refresh_rollups_for_days(session, dialect_name, days, roots)


async def _span(session: AsyncSession) -> tuple[datetime | None, datetime | None]:
    """Return the (min, max) final-attempt event timestamp, or (None, None)."""
    event = models.UsageEventV2
    low, high = (
        await session.execute(
            select(func.min(event.ts_started), func.max(event.ts_started)).where(
                event.event_kind == "attempt", event.finality == "final"
            )
        )
    ).one()
    return low, high


def _event_days(span: tuple[datetime | None, datetime | None]) -> list[date]:
    """Expand a (min, max) timestamp span into a list of UTC calendar days."""
    low, high = span
    if low is None or high is None:
        return []
    start = (low if low.tzinfo else low.replace(tzinfo=UTC)).date()
    end = (high if high.tzinfo else high.replace(tzinfo=UTC)).date()
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _cost_split_columns() -> list[ColumnElement[Any]]:
    """Conditional cost sums split by status and billing mode (FR-ROLLUP-007).

    Contributions are NULL (not 0) when a bucket has no members, so ``SUM``
    returns NULL for an all-unknown group -- preserving the pre-migration
    "cost unknown" semantics for parity. An event with no active cost row falls
    back to its transitional ``cost_usd`` in the priced bucket.
    """
    event = models.UsageEventV2
    cost = models.ComputedCost
    not_subscription = cost.billing_mode != "subscription"
    priced = case(
        (cost.id.is_(None), event.cost_usd),  # not yet costed: transitional cost
        (and_(not_subscription, cost.cost_status == "priced"), cost.amount),
        else_=None,
    )
    partial = case(
        (and_(cost.id.isnot(None), not_subscription, cost.cost_status == "partial"),
         cost.amount),
        else_=None,
    )
    estimated = case(
        (and_(cost.id.isnot(None), not_subscription, cost.cost_status == "estimated"),
         cost.amount),
        else_=None,
    )
    unpriced = case(
        (and_(cost.id.isnot(None), cost.cost_status.in_(_UNPRICED_STATUSES)), 1),
        else_=0,
    )
    subscription = case(
        (and_(cost.id.isnot(None), cost.billing_mode == "subscription"),
         cost.subscription_equivalent_amount),
        else_=None,
    )
    return [
        func.sum(priced),
        func.sum(partial),
        func.sum(estimated),
        func.sum(unpriced),
        func.sum(subscription),
    ]


async def _aggregate_day(
    session: AsyncSession, day: date, roots: Sequence[str] = DEFAULT_ROOTS
) -> list[dict[str, object]]:
    """Aggregate final-attempt events for one day into rollup row dicts.

    Grouped by the v2 grain ``(provider, native_model, machine, project-group,
    source, environment, billing_mode)`` (provenance is the derived-rollup
    marker). The raw ``cwd`` folds to a project group (:func:`project_group`) so
    worktrees and case variants collapse; absent dimensions use ``''`` sentinels.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    event = models.UsageEventV2
    cost = models.ComputedCost
    src = models.Source
    machine = func.coalesce(event.machine, "").label("machine")
    project = func.coalesce(event.project, "").label("project")
    environment = func.coalesce(event.environment, "").label("environment")
    source_name = func.coalesce(src.name, "").label("source")
    billing_mode = func.coalesce(cost.billing_mode, "api_billed").label("billing_mode")
    reasoning = func.sum(event.reasoning_tokens)
    total = func.sum(
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
        + event.reasoning_tokens
    )

    statement = (
        select(
            event.provider,
            event.native_model,
            machine,
            project,
            source_name,
            environment,
            billing_mode,
            func.sum(event.input_tokens),
            func.sum(event.output_tokens),
            func.sum(event.cache_read_tokens),
            func.sum(event.cache_write_short_tokens),
            func.sum(event.cache_write_long_tokens),
            reasoning,
            total,
            *_cost_split_columns(),
        )
        .select_from(event)
        .join(
            cost,
            and_(
                cost.provider == event.provider,
                cost.event_id == event.event_id,
                cost.active.is_(True),
            ),
            isouter=True,
        )
        .join(src, src.id == event.source_id, isouter=True)
        .where(
            event.event_kind == "attempt",
            event.finality == "final",
            event.ts_started >= day_start,
            event.ts_started < day_end,
        )
        .group_by(
            event.provider, event.native_model, machine, project,
            source_name, environment, billing_mode,
        )
    )

    result = await session.execute(statement)
    # Fold raw project directories into project groups, summing rows (worktrees,
    # case variants) that map to the same group; the rest of the grain is stable.
    merged: dict[tuple[str, ...], _Agg] = {}
    for record in result.all():
        group = project_group(str(record[3]), roots)
        key = (
            str(record[0]), str(record[1]), str(record[2]), group,
            str(record[4]), str(record[5]), str(record[6]),
        )
        tokens = [int(record[offset] or 0) for offset in range(7, 14)]
        agg = merged.get(key)
        if agg is None:
            agg = _Agg(tokens=tokens)
            merged[key] = agg
        else:
            agg.tokens = [a + b for a, b in zip(agg.tokens, tokens, strict=True)]
        agg.cost_priced = _add_cost(agg.cost_priced, _to_decimal(record[14]))
        agg.cost_partial = _add_cost(agg.cost_partial, _to_decimal(record[15]))
        agg.cost_estimated = _add_cost(agg.cost_estimated, _to_decimal(record[16]))
        agg.unpriced_count += int(record[17] or 0)
        agg.subscription_value = _add_cost(
            agg.subscription_value, _to_decimal(record[18])
        )

    rows: list[dict[str, object]] = []
    for (prov, mdl, mach, grp, source, env, bmode), agg in merged.items():
        cost_usd = _add_cost(
            _add_cost(agg.cost_priced, agg.cost_partial), agg.cost_estimated
        )
        row: dict[str, object] = {
            "day": day,
            "provider": prov,
            "model": mdl,
            "machine": mach,
            "project": grp,
            "source": source,
            "environment": env,
            "billing_mode": bmode,
            "provenance": DERIVED,
            "cost_usd": cost_usd,
            "cost_priced_usd": agg.cost_priced,
            "cost_partial_usd": agg.cost_partial,
            "cost_estimated_usd": agg.cost_estimated,
            "unpriced_event_count": agg.unpriced_count,
            "subscription_value_usd": agg.subscription_value,
        }
        row.update(dict(zip(_TOKEN_KEYS, agg.tokens, strict=True)))
        rows.append(row)
    return rows


def _to_decimal(value: object) -> Decimal | None:
    """Convert a nullable DB numeric to Decimal (None stays None)."""
    return None if value is None else Decimal(str(value))


def _add_cost(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    """Sum two nullable cost values, preserving None when both are unknown."""
    if left is None and right is None:
        return None
    return (left or Decimal("0")) + (right or Decimal("0"))
