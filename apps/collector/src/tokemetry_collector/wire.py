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
