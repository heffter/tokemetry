"""API request and response schemas for the ingest endpoints.

Wire models are deliberately separate from the core domain models: the
collector serializes core ``UsageEvent`` / ``LimitSnapshot`` /
``DailyAggregate`` objects, and these schemas validate the incoming JSON and
convert to core objects. Keeping them distinct lets the wire format evolve
independently of storage.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tokemetry_core.models import (
    DailyAggregate,
    LimitSnapshot,
    Provenance,
    UsageEvent,
)


class MachineInfo(BaseModel):
    """Identifying information for the reporting machine."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    platform: str | None = Field(default=None, max_length=50)
    collector_version: str | None = Field(default=None, max_length=50)


class UsageEventIn(BaseModel):
    """One usage event as sent by a collector."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=50)
    native_model: str = Field(min_length=1, max_length=200)
    ts: datetime
    session_id: str | None = Field(default=None, max_length=200)
    project: str | None = Field(default=None, max_length=500)
    git_branch: str | None = Field(default=None, max_length=300)
    client_version: str | None = Field(default=None, max_length=50)
    entrypoint: str | None = Field(default=None, max_length=50)
    is_sidechain: bool = False
    session_kind: str | None = Field(default=None, max_length=50)
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cache_read_tokens: int = Field(ge=0, default=0)
    cache_write_short_tokens: int = Field(ge=0, default=0)
    cache_write_long_tokens: int = Field(ge=0, default=0)
    service_tier: str | None = Field(default=None, max_length=50)
    speed: str | None = Field(default=None, max_length=50)
    provenance: Provenance = Provenance.LOCAL_ESTIMATE
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_core(self, machine: str) -> UsageEvent:
        """Convert to a core :class:`UsageEvent`, stamping the machine."""
        return UsageEvent(
            event_id=self.event_id,
            provider=self.provider,
            native_model=self.native_model,
            ts=self.ts,
            machine=machine,
            session_id=self.session_id,
            project=self.project,
            git_branch=self.git_branch,
            client_version=self.client_version,
            entrypoint=self.entrypoint,
            is_sidechain=self.is_sidechain,
            session_kind=self.session_kind,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_short_tokens=self.cache_write_short_tokens,
            cache_write_long_tokens=self.cache_write_long_tokens,
            service_tier=self.service_tier,
            speed=self.speed,
            provenance=self.provenance,
            extra=self.extra,
        )


class LimitSnapshotIn(BaseModel):
    """One limit-window snapshot as sent by a collector."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=50)
    ts: datetime
    window_kind: str = Field(min_length=1, max_length=50)
    utilization_pct: float = Field(ge=0.0)
    resets_at: datetime | None = None
    provenance: Provenance = Provenance.OFFICIAL
    raw: dict[str, Any] = Field(default_factory=dict)

    def to_core(self, machine: str) -> LimitSnapshot:
        """Convert to a core :class:`LimitSnapshot`, stamping the machine."""
        return LimitSnapshot(
            provider=self.provider,
            ts=self.ts,
            machine=machine,
            window_kind=self.window_kind,
            utilization_pct=self.utilization_pct,
            resets_at=self.resets_at,
            provenance=self.provenance,
            raw=self.raw,
        )


class DailyAggregateIn(BaseModel):
    """One bootstrap daily aggregate as sent by a collector."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=50)
    day: date
    native_model: str = Field(min_length=1, max_length=200)
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cache_read_tokens: int = Field(ge=0, default=0)
    cache_write_short_tokens: int = Field(ge=0, default=0)
    cache_write_long_tokens: int = Field(ge=0, default=0)
    total_tokens: int = Field(ge=0, default=0)
    message_count: int = Field(ge=0, default=0)

    def to_core(self, machine: str) -> DailyAggregate:
        """Convert to a core :class:`DailyAggregate`, stamping the machine."""
        return DailyAggregate(
            provider=self.provider,
            day=self.day,
            native_model=self.native_model,
            machine=machine,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_short_tokens=self.cache_write_short_tokens,
            cache_write_long_tokens=self.cache_write_long_tokens,
            total_tokens=self.total_tokens,
            message_count=self.message_count,
        )


class EventsIngest(BaseModel):
    """Batch of usage events from one machine."""

    model_config = ConfigDict(extra="forbid")

    machine: MachineInfo
    events: list[UsageEventIn] = Field(min_length=1, max_length=5000)


class LimitsIngest(BaseModel):
    """Batch of limit snapshots from one machine."""

    model_config = ConfigDict(extra="forbid")

    machine: MachineInfo
    snapshots: list[LimitSnapshotIn] = Field(min_length=1, max_length=1000)


class BootstrapIngest(BaseModel):
    """Batch of bootstrap daily aggregates from one machine."""

    model_config = ConfigDict(extra="forbid")

    machine: MachineInfo
    aggregates: list[DailyAggregateIn] = Field(min_length=1, max_length=20000)


class IngestResult(BaseModel):
    """Outcome of an ingest call."""

    accepted: int
    duplicates_merged: int = 0
