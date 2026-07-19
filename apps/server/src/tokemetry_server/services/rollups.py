"""Daily rollup refresh.

After events are ingested, the affected days' rollups are recomputed from
``usage_events`` and written to ``daily_rollups`` with
``provenance='derived'``. Recomputing whole days (rather than applying
deltas) is correct in the presence of keep-max updates: the aggregate always
reflects the current stored rows. A collector batch touches one or two days,
so the cost is small.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.projects import DEFAULT_ROOTS, project_group

from tokemetry_server.db import models
from tokemetry_server.db.upsert import daily_rollups_upsert

#: Provenance stamped on rollups computed from real events.
DERIVED = "derived"

#: Rollup token columns, in the order the aggregate query selects them.
_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_short_tokens",
    "cache_write_long_tokens",
    "total_tokens",
)


@dataclass
class _Agg:
    """Accumulated token totals (in ``_TOKEN_KEYS`` order) and cost for a group."""

    tokens: list[int]
    cost: Decimal | None


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
        days: Days to recompute (typically the days touched by a batch).
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
    """Return the (min, max) event timestamp, or (None, None) when empty."""
    event = models.UsageEvent
    low, high = (
        await session.execute(select(func.min(event.ts), func.max(event.ts)))
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


async def _aggregate_day(
    session: AsyncSession, day: date, roots: Sequence[str] = DEFAULT_ROOTS
) -> list[dict[str, object]]:
    """Aggregate usage_events for one day into rollup row dicts.

    Grouped by ``(provider, machine, model, project-group)``. The raw ``cwd``
    is folded to a project group (see :func:`project_group`) and rows sharing a
    group are summed, so worktrees and case-variant paths collapse into one
    project. Null machine/project use ``''`` sentinels to match the grain.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    event = models.UsageEvent
    machine = func.coalesce(event.machine, "").label("machine")
    project = func.coalesce(event.project, "").label("project")
    total = func.sum(
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )

    statement = (
        select(
            event.provider,
            machine,
            event.model,
            project,
            func.sum(event.input_tokens),
            func.sum(event.output_tokens),
            func.sum(event.cache_read_tokens),
            func.sum(event.cache_write_short_tokens),
            func.sum(event.cache_write_long_tokens),
            total,
            func.sum(event.cost_usd),
        )
        .where(event.ts >= day_start, event.ts < day_end)
        .group_by(event.provider, machine, event.model, project)
    )

    result = await session.execute(statement)
    # Fold raw project directories into project groups, summing the totals of
    # rows (worktrees, case variants) that map to the same group.
    merged: dict[tuple[str, str, str, str], _Agg] = {}
    for record in result.all():
        group = project_group(record[3], roots)
        key = (str(record[0]), str(record[1]), str(record[2]), group)
        tokens = [int(record[offset] or 0) for offset in range(4, 10)]
        cost = _to_decimal(record[10])
        agg = merged.get(key)
        if agg is None:
            merged[key] = _Agg(tokens=tokens, cost=cost)
        else:
            agg.tokens = [a + b for a, b in zip(agg.tokens, tokens, strict=True)]
            agg.cost = _add_cost(agg.cost, cost)

    rows: list[dict[str, object]] = []
    for (prov, mach, mdl, grp), agg in merged.items():
        # v2 grain sentinels: the v1-derived rollup carries no source,
        # environment, or per-source billing mode yet -- Task 66.2 reads
        # usage_events_v2/computed_costs to populate them and the cost split.
        row: dict[str, object] = {
            "day": day,
            "provider": prov,
            "model": mdl,
            "machine": mach,
            "project": grp,
            "source": "",
            "environment": "",
            "billing_mode": "api_billed",
            "provenance": DERIVED,
            "reasoning_tokens": 0,
            "cost_usd": agg.cost,
            "cost_priced_usd": agg.cost,
            "cost_partial_usd": None,
            "cost_estimated_usd": None,
            "unpriced_event_count": 0,
            "subscription_value_usd": None,
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
