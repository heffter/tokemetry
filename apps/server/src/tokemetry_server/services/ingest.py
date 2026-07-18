"""Ingest orchestration: dedup, validate, and idempotent persistence.

The service is transport-agnostic: it takes already-parsed core objects and
an :class:`~sqlalchemy.ext.asyncio.AsyncSession`, so it is exercised the
same way by the HTTP routes and by tests. Cost computation is injected as an
optional callable, kept out of this task's scope (see the cost engine task);
when absent, ``cost_usd`` is stored as ``NULL`` and filled in later.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.models import DailyAggregate, LimitSnapshot, UsageEvent
from tokemetry_core.projects import DEFAULT_ROOTS
from tokemetry_core.usage_v2 import (
    EventKind,
    Finality,
    SourceRef,
    SourceType,
    UsageEventV2,
)

from tokemetry_server.api.schemas import IngestResult, MachineInfo
from tokemetry_server.db import models
from tokemetry_server.db.upsert import (
    daily_rollups_upsert,
    machine_upsert,
    usage_events_upsert,
)
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.registries import (
    ModelRegistryService,
    ProviderRegistryService,
)
from tokemetry_server.services.revisions import ConflictMode, RevisionEngine
from tokemetry_server.services.rollups import refresh_rollups_for_days
from tokemetry_server.services.validation import ValidationError, validate_event, validate_limit

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


def _v1_to_v2(event: UsageEvent, machine: str) -> UsageEventV2:
    """Map a v1 core usage event onto a v2 wire event (FR-EVENT-023).

    Mirrors the backfill mapping (``db/backfill.py``): ``event_kind='attempt'``,
    ``finality='final'``, ``sequence=0``, no reasoning, and the v1-only fields
    preserved under ``extra['_v1']`` so the compatibility view reproduces them.
    A minimal collector source is synthesized (source attribution is Task 63).
    """
    extra = dict(event.extra)
    extra["_v1"] = {
        "git_branch": event.git_branch,
        "client_version": event.client_version,
        "entrypoint": event.entrypoint,
        "is_sidechain": event.is_sidechain,
        "session_kind": event.session_kind,
        "speed": event.speed,
        "source": "collector",
    }
    return UsageEventV2(
        schema_version=2,
        event_id=event.event_id,
        event_kind=EventKind.ATTEMPT,
        finality=Finality.FINAL,
        sequence=0,
        provider=event.provider,
        native_model=event.native_model,
        ts_started=event.ts,
        ts_completed=event.ts,
        machine=event.machine or machine,
        session_id=event.session_id,
        project=event.project,
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        cache_read_tokens=event.cache_read_tokens,
        cache_write_short_tokens=event.cache_write_short_tokens,
        cache_write_long_tokens=event.cache_write_long_tokens,
        reasoning_tokens=0,
        success=True,
        service_tier=event.service_tier,
        provenance=event.provenance,
        source=SourceRef(type=SourceType.COLLECTOR, name="collector", version="v1"),
        extra=extra,
    )


class IngestService:
    """Persists usage events, limit snapshots, and bootstrap aggregates."""

    def __init__(
        self,
        session: AsyncSession,
        dialect_name: str,
        cost_fn: CostFn | None = None,
        roots: Sequence[str] = DEFAULT_ROOTS,
        providers: ProviderRegistryService | None = None,
        models_registry: ModelRegistryService | None = None,
        data_quality: DataQualityService | None = None,
        unknown_provider_policy: str = "accept",
    ) -> None:
        """Create the service.

        Args:
            session: Active async session; the caller owns the transaction.
            dialect_name: ``"postgresql"`` or ``"sqlite"`` for upsert syntax.
            cost_fn: Optional per-event cost function.
            roots: Project root markers for directory-to-project grouping.
            providers: Optional provider registry service; when supplied,
                ingest applies the unknown-provider policy and records the
                provider in the registry.
            models_registry: Optional model registry service; when supplied,
                ingest observes each event's native model (advancing
                ``last_seen`` or inserting an unknown model).
            data_quality: Optional data-quality sink; when supplied, ingest
                records unknown providers/models fire-and-forget (a recording
                failure never rejects otherwise-valid events).
            unknown_provider_policy: ``"accept"`` or ``"reject"``; only
                consulted when ``providers`` is supplied.
        """
        self._session = session
        self._dialect = dialect_name
        self._cost_fn = cost_fn
        self._roots = roots
        self._providers = providers
        self._models_registry = models_registry
        self._data_quality = data_quality
        self._unknown_provider_policy = unknown_provider_policy

    async def ingest_events(
        self, machine: MachineInfo, events: list[UsageEvent]
    ) -> IngestResult:
        """Validate and upsert a batch of usage events (keep-max)."""
        for event in events:
            validate_event(event)
        deduped = _dedupe_keep_max(events)
        await self._apply_registry_policy(deduped)
        await self._touch_machine(machine)

        rows = [self._event_row(event, machine.name) for event in deduped]
        stmt = usage_events_upsert(self._dialect, models.UsageEvent.__table__, rows)
        await self._session.execute(stmt)

        # Mirror the batch into the v2 ledger through the revision engine in
        # keep-max compatibility mode (FR-IDEMP-012), so the ledger tracks v1
        # ingest ahead of the read swap to the compatibility view (subtask
        # 62.10). The physical usage_events table above remains the read source
        # until that swap, keeping v1 responses byte-identical.
        await self._mirror_to_v2(deduped, machine.name)

        # Recompute the touched days' rollups from the now-current events.
        affected_days = {event.ts.date() for event in deduped}
        await refresh_rollups_for_days(
            self._session, self._dialect, affected_days, self._roots
        )

        return IngestResult(
            accepted=len(deduped),
            duplicates_merged=len(events) - len(deduped),
        )

    async def _mirror_to_v2(self, events: list[UsageEvent], machine: str) -> None:
        """Project deduped v1 events into ``usage_events_v2`` (keep-max mode)."""
        engine = RevisionEngine(self._session, self._data_quality)
        for event in events:
            cost = self._cost_fn(event) if self._cost_fn is not None else None
            await engine.apply(
                _v1_to_v2(event, machine),
                mode=ConflictMode.KEEP_MAX,
                cost=cost,
            )

    async def _apply_registry_policy(self, events: list[UsageEvent]) -> None:
        """Enforce the provider policy and observe models for ``events``.

        No-op unless the registry services were injected. Provider resolution
        runs per distinct provider (rejecting the whole batch when the policy
        forbids an unregistered one); model observation runs per distinct
        ``(provider, native_model)`` at that pair's newest timestamp. Unknown
        providers and models are recorded as data-quality events
        fire-and-forget (FR-MODEL-006), never failing accepted ingest.
        """
        if self._providers is None and self._models_registry is None:
            return

        latest: dict[tuple[str, str], datetime] = {}
        provider_ts: dict[str, datetime] = {}
        for event in events:
            key = (event.provider, event.native_model)
            if key not in latest or event.ts > latest[key]:
                latest[key] = event.ts
            if event.provider not in provider_ts or event.ts > provider_ts[event.provider]:
                provider_ts[event.provider] = event.ts

        if self._providers is not None:
            for provider, ts in provider_ts.items():
                resolution = await self._providers.resolve(
                    provider, self._unknown_provider_policy
                )
                if not resolution.accepted:
                    raise ValidationError(
                        f"provider '{provider}' is not registered and the "
                        "unknown-provider policy is 'reject'"
                    )
                if not resolution.registered and self._data_quality is not None:
                    await self._data_quality.record_safe(
                        "unknown_provider", resolution.provider, ts, detail={"raw": provider}
                    )

        if self._models_registry is not None:
            for (provider, native_model), ts in latest.items():
                observation = await self._models_registry.observe(provider, native_model, ts)
                if observation.newly_observed and self._data_quality is not None:
                    await self._data_quality.record_safe(
                        "unknown_model",
                        f"{provider}/{native_model}",
                        ts,
                        detail={"provider": provider, "native_model": native_model},
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
