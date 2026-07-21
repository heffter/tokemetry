"""Serialization of core objects into the server's ingest wire format.

The collector produces plain JSON-compatible dicts matching the server's
ingest schemas. The reporting machine is attached once per batch (the server
stamps it onto each row), so per-object dicts omit the machine field.
"""

from __future__ import annotations

from typing import Any

from tokemetry_core.models import DailyAggregate, LimitSnapshot, UsageEvent

from tokemetry_collector import __version__
from tokemetry_collector.config import CollectorConfig


def machine_info(config: CollectorConfig) -> dict[str, Any]:
    """Build the per-batch machine descriptor."""
    return {
        "name": config.machine_name,
        "platform": config.machine_platform,
        "collector_version": __version__,
    }


def collector_source(config: CollectorConfig) -> dict[str, Any]:
    """The collector's ``SourceRef`` identity for v2 ingest (Task 76).

    A collector-type source named for this machine, so the server resolves it to
    a stable ``source_id`` and the v2 limit dimensions land in their columns.
    """
    return {
        "type": "collector",
        "name": config.machine_name,
        "version": __version__,
        "instance_id": config.machine_name,
    }


def limit_to_wire_v2(
    snapshot: LimitSnapshot, machine: str, source: dict[str, Any]
) -> dict[str, Any]:
    """Serialize a limit snapshot to the v2 ``LimitSnapshotV2`` wire shape.

    Carries the account/organization/limit_amount/remaining/unit dimensions so
    they populate the dedicated ``limit_snapshots`` columns (Task 69.2) instead
    of riding in ``raw``. ``machine`` and ``source`` are per-snapshot in v2.
    """
    return {
        "schema_version": 2,
        "provider": snapshot.provider,
        "window_kind": snapshot.window_kind,
        "ts": snapshot.ts.isoformat(),
        "utilization_pct": snapshot.utilization_pct,
        "machine": snapshot.machine or machine,
        "source": source,
        "account": snapshot.account,
        "organization": snapshot.organization,
        "limit_amount": snapshot.limit_amount,
        "remaining": snapshot.remaining,
        "unit": snapshot.unit,
        "resets_at": snapshot.resets_at.isoformat() if snapshot.resets_at else None,
        "provenance": str(snapshot.provenance),
    }


def event_to_wire(event: UsageEvent) -> dict[str, Any]:
    """Serialize a usage event (machine omitted; batch carries it)."""
    return {
        "event_id": event.event_id,
        "provider": event.provider,
        "native_model": event.native_model,
        "ts": event.ts.isoformat(),
        "session_id": event.session_id,
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
        "provenance": str(event.provenance),
        "extra": event.extra,
    }


def limit_to_wire(snapshot: LimitSnapshot) -> dict[str, Any]:
    """Serialize a limit snapshot (machine omitted; batch carries it)."""
    return {
        "provider": snapshot.provider,
        "ts": snapshot.ts.isoformat(),
        "window_kind": snapshot.window_kind,
        "utilization_pct": snapshot.utilization_pct,
        "resets_at": snapshot.resets_at.isoformat() if snapshot.resets_at else None,
        "provenance": str(snapshot.provenance),
        "raw": snapshot.raw,
    }


def aggregate_to_wire(aggregate: DailyAggregate) -> dict[str, Any]:
    """Serialize a bootstrap daily aggregate (machine omitted)."""
    return {
        "provider": aggregate.provider,
        "day": aggregate.day.isoformat(),
        "native_model": aggregate.native_model,
        "input_tokens": aggregate.input_tokens,
        "output_tokens": aggregate.output_tokens,
        "cache_read_tokens": aggregate.cache_read_tokens,
        "cache_write_short_tokens": aggregate.cache_write_short_tokens,
        "cache_write_long_tokens": aggregate.cache_write_long_tokens,
        "total_tokens": aggregate.total_tokens,
        "message_count": aggregate.message_count,
    }
