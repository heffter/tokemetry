"""Normalized domain models shared by collectors and the server.

Every provider adapter normalizes its native records into these models, so
the rest of the system (queue, ingest, engines, API) never needs to know
provider specifics. Money is represented as ``Decimal`` and token prices are
per million tokens (MTok). All timestamps must be timezone-aware.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class Provenance(enum.StrEnum):
    """Origin of a number, so the UI can label official vs estimated data.

    OFFICIAL: read from a provider's authoritative endpoint.
    LOCAL_ESTIMATE: derived from local artifacts (for example transcripts).
    STATS_CACHE: imported once from a provider's local aggregate cache.
    """

    OFFICIAL = "official"
    LOCAL_ESTIMATE = "local_estimate"
    STATS_CACHE = "stats_cache"


class _FrozenModel(BaseModel):
    """Base for immutable value objects with strict validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_tz(value: datetime) -> datetime:
    """Reject naive datetimes; every timestamp in the system is tz-aware."""
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class UsageEvent(_FrozenModel):
    """One normalized usage record (typically one provider API request).

    ``event_id`` must be unique within a provider (for Claude Code it is the
    transcript ``requestId``); the server deduplicates on
    ``(provider, event_id)`` keeping the row with the most output tokens.
    Provider-specific counters that have no generic column (for example web
    search request counts) go into ``extra``.
    """

    event_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    native_model: str = Field(min_length=1)
    ts: datetime
    machine: str | None = None
    session_id: str | None = None
    project: str | None = None
    git_branch: str | None = None
    client_version: str | None = None
    entrypoint: str | None = None
    is_sidechain: bool = False
    session_kind: str | None = None
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cache_read_tokens: int = Field(ge=0, default=0)
    cache_write_short_tokens: int = Field(ge=0, default=0)
    cache_write_long_tokens: int = Field(ge=0, default=0)
    service_tier: str | None = None
    speed: str | None = None
    provenance: Provenance = Provenance.LOCAL_ESTIMATE
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def _validate_ts(cls, value: datetime) -> datetime:
        """Timestamps must be timezone-aware."""
        return _require_tz(value)

    @property
    def total_tokens(self) -> int:
        """Sum of all token categories in this event."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_short_tokens
            + self.cache_write_long_tokens
        )


class DailyAggregate(_FrozenModel):
    """Per-day, per-model token totals used for history bootstrap imports.

    Providers that keep a local aggregate cache (Claude Code's
    ``stats-cache.json``) can expose history that predates their raw
    transcript retention through this coarser record.
    """

    provider: str = Field(min_length=1)
    day: date
    native_model: str = Field(min_length=1)
    machine: str | None = None
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cache_read_tokens: int = Field(ge=0, default=0)
    cache_write_short_tokens: int = Field(ge=0, default=0)
    cache_write_long_tokens: int = Field(ge=0, default=0)
    total_tokens: int = Field(ge=0, default=0, validate_default=True)
    message_count: int = Field(ge=0, default=0)
    provenance: Provenance = Provenance.STATS_CACHE

    @field_validator("total_tokens")
    @classmethod
    def _default_total(cls, value: int, info: ValidationInfo) -> int:
        """Derive the total from the split fields when not given.

        Some aggregate caches only publish per-day totals without an
        input/output split; those sources set ``total_tokens`` directly and
        leave the split fields at zero.
        """
        if value:
            return value
        return int(
            info.data.get("input_tokens", 0)
            + info.data.get("output_tokens", 0)
            + info.data.get("cache_read_tokens", 0)
            + info.data.get("cache_write_short_tokens", 0)
            + info.data.get("cache_write_long_tokens", 0)
        )


class LimitSnapshot(_FrozenModel):
    """Utilization of one provider rate-limit window at one point in time.

    ``window_kind`` is provider-defined (Anthropic emits ``five_hour``,
    ``seven_day``, ``seven_day_opus``, ``seven_day_sonnet``,
    ``extra_credits``); consumers treat it as an opaque label so new
    providers need no schema change. ``raw`` preserves the original payload
    for debugging and future reinterpretation.
    """

    provider: str = Field(min_length=1)
    ts: datetime
    machine: str | None = None
    window_kind: str = Field(min_length=1)
    utilization_pct: float = Field(ge=0.0)
    resets_at: datetime | None = None
    provenance: Provenance = Provenance.OFFICIAL
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def _validate_ts(cls, value: datetime) -> datetime:
        """Timestamps must be timezone-aware."""
        return _require_tz(value)

    @field_validator("resets_at")
    @classmethod
    def _resets_tz(cls, value: datetime | None) -> datetime | None:
        """Reset times, when present, must also be timezone-aware."""
        if value is None:
            return None
        return _require_tz(value)


class SourceFile(_FrozenModel):
    """A discovered artifact a usage source can parse incrementally.

    ``path`` identifies the artifact; ``size`` lets the collector detect
    truncation/rotation (size below the stored offset means start over).
    """

    path: Path
    size: int = Field(ge=0)


class ParseResult(_FrozenModel):
    """Outcome of one incremental parse pass over a source file.

    ``new_offset`` is the byte position the next parse should resume from;
    it is returned together with the events so the collector can persist
    both atomically (events queued and offset advanced, or neither).
    ``malformed_lines`` counts records that could not be parsed -- surfaced
    as a schema-drift indicator, never silently dropped.
    """

    events: tuple[UsageEvent, ...] = ()
    new_offset: int = Field(ge=0, default=0)
    malformed_lines: int = Field(ge=0, default=0)


class PriceRow(_FrozenModel):
    """Per-MTok prices for one provider model, effective from a given date.

    Prices are date-versioned: cost is always computed with the row whose
    ``effective_date`` is the latest one not after the event timestamp, so
    historical costs stay correct when prices change.
    """

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effective_date: date
    input_per_mtok: Decimal = Field(ge=0)
    output_per_mtok: Decimal = Field(ge=0)
    cache_read_per_mtok: Decimal = Field(ge=0)
    cache_write_short_per_mtok: Decimal = Field(ge=0)
    cache_write_long_per_mtok: Decimal = Field(ge=0)


class ProviderDescriptor(_FrozenModel):
    """Canonical registry metadata for one provider.

    ``id`` is the lowercase, stable identifier (FR-PROVIDER-002); ``aliases``
    are alternate spellings that normalize to it (FR-PROVIDER-003, resolved by
    :func:`tokemetry_core.normalization.normalize_provider`). The remaining
    fields are the metadata FR-PROVIDER-004 requires so later epics resolve
    pricing, limit-window semantics, and supported query dimensions from the
    registry instead of provider-specific code.
    """

    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    pricing_strategy: str = ""
    limit_semantics: str = "none"
    supported_dimensions: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def _canonical_id(cls, value: str) -> str:
        """Provider ids must be lowercase, stripped, stable identifiers."""
        if value != value.strip().lower():
            raise ValueError("provider id must be lowercase and stripped")
        return value

    @field_validator("aliases")
    @classmethod
    def _lower_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Aliases are matched case-insensitively; store them normalized."""
        return tuple(alias.strip().lower() for alias in value)
