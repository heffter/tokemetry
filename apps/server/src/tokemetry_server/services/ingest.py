"""Ingest orchestration: dedup, validate, and idempotent persistence.

The service is transport-agnostic: it takes already-parsed core objects and
an :class:`~sqlalchemy.ext.asyncio.AsyncSession`, so it is exercised the
same way by the HTTP routes and by tests. Cost computation is injected as an
optional callable, kept out of this task's scope (see the cost engine task);
when absent, ``cost_usd`` is stored as ``NULL`` and filled in later.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import DailyAggregate, LimitSnapshot, UsageEvent

from tokemetry_server.api.schemas import IngestResult, MachineInfo
from tokemetry_server.db import models
from tokemetry_server.db.upsert import (
    daily_rollups_upsert,
    machine_upsert,
    usage_events_upsert,
)
from tokemetry_server.services.rollups import refresh_rollups_for_days
from tokemetry_server.services.validation import validate_event, validate_limit

#: Callable computing an event's USD cost, or None when no price is known.
CostFn = Callable[[UsageEvent], "Decimal | None"]


def _dedupe_keep_max(events: list[UsageEvent]) -> list[UsageEvent]:
    """Collapse events sharing ``(provider, event_id)`` keeping max output.

    Required before upsert: a batch must not target the same conflict key
    twice (Postgres rejects it), and keep-max matches the parser's dedup.
    """
    best: dict[tuple[str, str], UsageEvent] = {}
    order: list[tuple[str, str]] = []
    for event in events:
        key = (event.provider, event.event_id)
        current = best.get(key)
        if current is None:
            order.append(key)
            best[key] = event
        elif event.output_tokens >= current.output_tokens:
            best[key] = event
    return [best[key] for key in order]


class IngestService:
    """Persists usage events, limit snapshots, and bootstrap aggregates."""

    def __init__(
        self,
        session: AsyncSession,
        dialect_name: str,
        cost_fn: CostFn | None = None,
    ) -> None:
        """Create the service.

        Args:
            session: Active async session; the caller owns the transaction.
            dialect_name: ``"postgresql"`` or ``"sqlite"`` for upsert syntax.
            cost_fn: Optional per-event cost function.
        """
        self._session = session
        self._dialect = dialect_name
        self._cost_fn = cost_fn

    async def ingest_events(
        self, machine: MachineInfo, events: list[UsageEvent]
    ) -> IngestResult:
        """Validate and upsert a batch of usage events (keep-max)."""
        for event in events:
            validate_event(event)
        deduped = _dedupe_keep_max(events)
        await self._touch_machine(machine)

        rows = [self._event_row(event, machine.name) for event in deduped]
        stmt = usage_events_upsert(self._dialect, models.UsageEvent.__table__, rows)
        await self._session.execute(stmt)

        # Recompute the touched days' rollups from the now-current events.
        affected_days = {event.ts.date() for event in deduped}
        await refresh_rollups_for_days(self._session, self._dialect, affected_days)

        return IngestResult(
            accepted=len(deduped),
            duplicates_merged=len(events) - len(deduped),
        )

    async def ingest_limits(
        self, machine: MachineInfo, snapshots: list[LimitSnapshot]
    ) -> IngestResult:
        """Validate and append a batch of limit snapshots."""
        for snapshot in snapshots:
            validate_limit(snapshot)
        await self._touch_machine(machine)

        self._session.add_all(
            models.LimitSnapshot(
                provider=snapshot.provider,
                machine=machine.name,
                ts=snapshot.ts,
                window_kind=snapshot.window_kind,
                utilization_pct=snapshot.utilization_pct,
                resets_at=snapshot.resets_at,
                provenance=str(snapshot.provenance),
                raw=snapshot.raw,
            )
            for snapshot in snapshots
        )
        return IngestResult(accepted=len(snapshots))

    async def ingest_bootstrap(
        self, machine: MachineInfo, aggregates: list[DailyAggregate]
    ) -> IngestResult:
        """Upsert bootstrap daily aggregates into the rollup table."""
        await self._touch_machine(machine)
        rows = [self._rollup_row(aggregate, machine.name) for aggregate in aggregates]
        stmt = daily_rollups_upsert(self._dialect, models.DailyRollup.__table__, rows)
        await self._session.execute(stmt)
        return IngestResult(accepted=len(aggregates))

    async def _touch_machine(self, machine: MachineInfo) -> None:
        """Register or refresh the reporting machine's metadata."""
        now = datetime.now(UTC)
        row = {
            "id": machine.name,
            "platform": machine.platform,
            "first_seen": now,
            "last_seen": now,
            "collector_version": machine.collector_version,
        }
        stmt = machine_upsert(self._dialect, models.Machine.__table__, row)
        await self._session.execute(stmt)

    def _event_row(self, event: UsageEvent, machine: str) -> dict[str, object]:
        """Build a usage_events row dict from a core event."""
        cost = self._cost_fn(event) if self._cost_fn is not None else None
        return {
            "provider": event.provider,
            "event_id": event.event_id,
            "machine": machine,
            "session_id": event.session_id,
            "ts": event.ts,
            "model": event.native_model,
            "project": event.project,
            "git_branch": event.git_branch,
            "client_version": event.client_version,
            "entrypoint": event.entrypoint,
            "is_sidechain": event.is_sidechain,
            "session_kind": event.session_kind,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "cache_read_tokens": event.cache_read_tokens,
            "cache_write_short_tokens": event.cache_write_short_tokens,
            "cache_write_long_tokens": event.cache_write_long_tokens,
            "service_tier": event.service_tier,
            "speed": event.speed,
            "cost_usd": cost,
            "provenance": str(event.provenance),
            "source": "collector",
            "extra": event.extra,
        }

    def _rollup_row(self, aggregate: DailyAggregate, machine: str) -> dict[str, object]:
        """Build a daily_rollups row dict from a bootstrap aggregate."""
        return {
            "day": aggregate.day,
            "provider": aggregate.provider,
            "machine": aggregate.machine or machine,
            "model": aggregate.native_model,
            "project": "",
            "input_tokens": aggregate.input_tokens,
            "output_tokens": aggregate.output_tokens,
            "cache_read_tokens": aggregate.cache_read_tokens,
            "cache_write_short_tokens": aggregate.cache_write_short_tokens,
            "cache_write_long_tokens": aggregate.cache_write_long_tokens,
            "total_tokens": aggregate.total_tokens,
            "cost_usd": None,
            "provenance": str(aggregate.provenance),
        }
