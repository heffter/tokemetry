"""SQLAlchemy ORM models: the tokemetry storage schema.

The schema is intentionally a set of plain, timestamped tables (no
database-specific features beyond a JSON/JSONB column type) so Grafana can
be pointed straight at the database. Token counts use ``BigInteger`` because
cache-read totals reach billions; money uses ``Numeric(20, 10)`` for exact
micro-USD arithmetic.

Grain and idempotency:

- ``usage_events`` primary key is ``(provider, event_id)`` -- the natural
  idempotency key; ingest upserts keep the row with the most output tokens.
- ``daily_rollups`` is unique on its full grain
  ``(day, provider, machine, model, project)`` with ``''`` sentinels for
  absent dimensions so upserts are deterministic across dialects.
- ``pricing`` is unique on ``(provider, model, effective_date)``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tokemetry_server.db.base import Base, JSONType

#: Precision for money columns: 20 digits, 10 after the decimal point.
_MONEY = Numeric(20, 10)


class Machine(Base):
    """A machine running a collector."""

    __tablename__ = "machines"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    platform: Mapped[str | None] = mapped_column(String(50))
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collector_version: Mapped[str | None] = mapped_column(String(50))


class UsageEvent(Base):
    """One normalized provider usage record (typically one API request)."""

    __tablename__ = "usage_events"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    machine: Mapped[str | None] = mapped_column(String(200), index=True)
    session_id: Mapped[str | None] = mapped_column(String(200), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    model: Mapped[str] = mapped_column(String(200), index=True)
    project: Mapped[str | None] = mapped_column(String(500))
    git_branch: Mapped[str | None] = mapped_column(String(300))
    client_version: Mapped[str | None] = mapped_column(String(50))
    entrypoint: Mapped[str | None] = mapped_column(String(50))
    is_sidechain: Mapped[bool] = mapped_column(Boolean, default=False)
    session_kind: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_short_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_long_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    service_tier: Mapped[str | None] = mapped_column(String(50))
    speed: Mapped[str | None] = mapped_column(String(50))
    cost_usd: Mapped[Decimal | None] = mapped_column(_MONEY)
    provenance: Mapped[str] = mapped_column(String(30))
    source: Mapped[str | None] = mapped_column(String(50))
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class UsageEventV2(Base):
    """The active (current) state of one v2 attempt-level usage event.

    Keyed by ``(provider, event_id)`` like v1, but flattened from the full v2
    wire model (``tokemetry_core.usage_v2.UsageEventV2``): finality/sequence for
    streamed snapshots, separate requested/routed/native models, reasoning
    tokens, success/outcome, and OpenTelemetry ids. Superseded and conflicting
    states are archived in ``usage_event_revisions`` (the revision engine, task
    62.4). ``source_id`` is a plain integer reference to the ``sources`` table
    that Task 63 adds; the foreign key is introduced with that table so this
    migration stays self-contained.
    """

    __tablename__ = "usage_events_v2"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    event_kind: Mapped[str] = mapped_column(String(30))
    finality: Mapped[str] = mapped_column(String(20))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    logical_request_id: Mapped[str | None] = mapped_column(String(200), index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(200))
    provider_request_id: Mapped[str | None] = mapped_column(String(200), index=True)
    provider_response_id: Mapped[str | None] = mapped_column(String(200))
    requested_model: Mapped[str | None] = mapped_column(String(200))
    routed_model: Mapped[str | None] = mapped_column(String(200))
    native_model: Mapped[str] = mapped_column(String(200), index=True)
    ts_started: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ts_first_token: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ts_completed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    machine: Mapped[str | None] = mapped_column(String(200), index=True)
    project: Mapped[str | None] = mapped_column(String(500))
    session_id: Mapped[str | None] = mapped_column(String(200), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(200))
    environment: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_short_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_long_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str | None] = mapped_column(String(50), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    stop_reason: Mapped[str | None] = mapped_column(String(50))
    service_tier: Mapped[str | None] = mapped_column(String(50))
    streaming: Mapped[bool | None] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    time_to_first_token_ms: Mapped[int | None] = mapped_column(Integer)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_histogram: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    provenance: Mapped[str] = mapped_column(String(30))
    # Transitional: preserves the v1 keep-max cost so the v1 compatibility view
    # exposes cost during migration. Cost moves to computed_costs in Task 64,
    # which drops this column (design Section 3.4).
    cost_usd: Mapped[Decimal | None] = mapped_column(_MONEY)
    source_id: Mapped[int | None] = mapped_column(Integer, index=True)
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(200), index=True)
    span_id: Mapped[str | None] = mapped_column(String(200), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(200), index=True)


class UsageEventRevision(Base):
    """An archived superseded, conflicting, or corrected event state.

    Every time a newer event replaces the active row -- a higher-sequence
    snapshot, a final over a snapshot, a rejected same-sequence conflict, or an
    admin correction -- the prior state is written here with a ``reason`` of
    ``superseded``, ``conflict``, or ``correction`` and the ``actor`` that
    caused it, so the full history of an event id is auditable (FR-IDEMP-006).
    Indexed by ``(provider, event_id)`` for per-event history lookups.
    """

    __tablename__ = "usage_event_revisions"
    __table_args__ = (
        Index("ix_usage_event_revisions_provider_event", "provider", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    event_id: Mapped[str] = mapped_column(String(200))
    sequence: Mapped[int] = mapped_column(Integer)
    finality: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    reason: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str | None] = mapped_column(String(200))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BillableUnit(Base):
    """A non-token billable quantity for one event (FR-DIM-008, D-006).

    Hosted-tool charges and media/storage units that have no dedicated counter
    column (``web_search_request``, ``image_input``, ``audio_output_second``,
    ...). Token units are never stored here -- they are priced from the event
    counters (PP-007). One row per ``(provider, event_id, unit_type)``; the
    revision engine replaces an event's units atomically when it is superseded.
    """

    __tablename__ = "billable_units"
    __table_args__ = (
        UniqueConstraint(
            "provider", "event_id", "unit_type", name="billable_units_grain"
        ),
        ForeignKeyConstraint(
            ["provider", "event_id"],
            ["usage_events_v2.provider", "usage_events_v2.event_id"],
            name="fk_billable_units_event",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    event_id: Mapped[str] = mapped_column(String(200))
    unit_type: Mapped[str] = mapped_column(String(50))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))


class ComputedCost(Base):
    """Cost for one event, kept separate from the usage facts (D-006, TOK-5).

    Cost never lives on the usage row: a ``computed_costs`` row records the
    amount for one ``(provider, event_id, pricing_version)`` with a
    ``cost_status`` (``priced``/``unpriced``/``partial``/``estimated``/``error``,
    FR-COST-006), the ``billing_mode`` cost split (D-007), an optional
    ``subscription_equivalent_amount``, ``missing_units`` for partial cost
    (FR-COST-007), and an ``observed_cost`` for exporter reconciliation
    (FR-COST-003, D-016). Exactly one row per event is ``active`` -- the current
    authoritative cost -- enforced in the service layer (FR-COST-001).
    """

    __tablename__ = "computed_costs"
    __table_args__ = (
        UniqueConstraint(
            "provider", "event_id", "pricing_version", name="computed_costs_grain"
        ),
        ForeignKeyConstraint(
            ["provider", "event_id"],
            ["usage_events_v2.provider", "usage_events_v2.event_id"],
            name="fk_computed_costs_event",
        ),
        Index("ix_computed_costs_event", "provider", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    event_id: Mapped[str] = mapped_column(String(200))
    pricing_version: Mapped[str] = mapped_column(String(50))
    cost_status: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal | None] = mapped_column(_MONEY)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    billing_mode: Mapped[str] = mapped_column(String(20), default="api_billed")
    subscription_equivalent_amount: Mapped[Decimal | None] = mapped_column(_MONEY)
    missing_units: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    observed_cost: Mapped[Decimal | None] = mapped_column(_MONEY)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LogicalRequest(Base):
    """A non-billable grouping of the attempts of one logical request (D-003).

    Keyed by ``(provider, logical_request_id)``. Populated and maintained from
    attempt events (task 62.11): ``attempt_count`` and ``fallback_count`` track
    the chain, ``winning_attempt_id`` names the attempt whose usage is billed,
    and ``ts_first``/``ts_last`` bound the request. Usage is never stored here
    -- only on the attempt rows (FR-EVENT-004).
    """

    __tablename__ = "logical_requests"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    logical_request_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    requested_model: Mapped[str | None] = mapped_column(String(200))
    session_id: Mapped[str | None] = mapped_column(String(200))
    routing_policy: Mapped[str | None] = mapped_column(String(100))
    routing_reason: Mapped[str | None] = mapped_column(String(100))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    fallback_count: Mapped[int] = mapped_column(Integer, default=0)
    winning_attempt_id: Mapped[str | None] = mapped_column(String(200))
    ts_first: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ts_last: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestBatch(Base):
    """One v2 ingest batch's operational record (FR-INGEST-008).

    Written after each batch resolves so ingest is traceable: the
    server-generated ``batch_id``, the reporting source and token label, the
    per-outcome counts (FR-IDEMP-011), the schema version, and the response's
    ``request_id`` (FR-INGEST-016). Retained per the retention defaults (Task
    70); carries no event content. ``source_id`` is a plain integer reference
    until Task 63 adds the ``sources`` table.
    """

    __tablename__ = "ingest_batches"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[int | None] = mapped_column(Integer)
    token_label: Mapped[str | None] = mapped_column(String(200))
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    corrected: Mapped[int] = mapped_column(Integer, default=0)
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))


class Source(Base):
    """A reporting source: a collector, gateway, SDK, importer, or manual actor.

    Source identity is distinct from machine identity (FR-SOURCE-003): one
    machine may host several sources (FR-SOURCE-009), so the grain is
    ``(type, name, instance_id)``. Auto-registered from v2 payloads on first
    sight (D-011); ``last_seen``/``version`` advance thereafter. ``token_label``
    and ``revoked`` support least-privilege ingest; revoking a source stops
    accepting its events but never deletes history (FR-SOURCE-012).
    ``billing_mode`` carries the D-007 cost split (``api_billed`` vs
    ``subscription``).
    """

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("type", "name", "instance_id", name="sources_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(50))
    instance_id: Mapped[str | None] = mapped_column(String(200))
    machine: Mapped[str | None] = mapped_column(String(200))
    token_label: Mapped[str | None] = mapped_column(String(200))
    billing_mode: Mapped[str] = mapped_column(String(20), default="api_billed")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Health state, updated per batch and evaluated at query time (task 63.2).
    last_successful_ingest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recent_error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_schema_version: Mapped[int | None] = mapped_column(Integer)
    clock_skew_seconds: Mapped[float | None] = mapped_column(Float)


class LimitSnapshot(Base):
    """Utilization of one provider limit window at one point in time."""

    __tablename__ = "limit_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    machine: Mapped[str | None] = mapped_column(String(200), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_kind: Mapped[str] = mapped_column(String(50), index=True)
    utilization_pct: Mapped[float] = mapped_column(Numeric(7, 3))
    resets_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[str] = mapped_column(String(30))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class Session(Base):
    """A Claude Code (or other provider) session, aggregated from events."""

    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    machine: Mapped[str | None] = mapped_column(String(200), index=True)
    project: Mapped[str | None] = mapped_column(String(500))
    slug: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_short_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_long_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(_MONEY)


class DailyRollup(Base):
    """Per-day, per-grain token and cost totals for fast history queries.

    The provider-neutral v2 grain (FR-ROLLUP-004/005) is ``(day, provider,
    model, machine, project, source, environment, billing_mode, provenance)``;
    absent dimensions use ``''`` sentinels (``api_billed`` for ``billing_mode``)
    so the unique grain holds identically on SQLite and Postgres. Cost is split
    by status -- ``cost_priced_usd``/``cost_partial_usd``/``cost_estimated_usd``
    plus ``unpriced_event_count`` and ``subscription_value_usd`` for the dual
    metric (FR-ROLLUP-007, FR-COST-012). ``cost_usd`` is retained transitionally
    (v1 keep-max cost) until the rollup service reads ``computed_costs`` (Task
    66.2); it currently mirrors ``cost_priced_usd``.
    """

    __tablename__ = "daily_rollups"
    __table_args__ = (
        UniqueConstraint(
            "day",
            "provider",
            "model",
            "machine",
            "project",
            "source",
            "environment",
            "billing_mode",
            "provenance",
            name="daily_rollups_grain",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200), default="")
    machine: Mapped[str] = mapped_column(String(200), default="")
    project: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    environment: Mapped[str] = mapped_column(String(50), default="")
    billing_mode: Mapped[str] = mapped_column(String(20), default="api_billed")
    provenance: Mapped[str] = mapped_column(String(30), default="derived")
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_short_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_long_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(_MONEY)
    cost_priced_usd: Mapped[Decimal | None] = mapped_column(_MONEY)
    cost_partial_usd: Mapped[Decimal | None] = mapped_column(_MONEY)
    cost_estimated_usd: Mapped[Decimal | None] = mapped_column(_MONEY)
    unpriced_event_count: Mapped[int] = mapped_column(Integer, default=0)
    subscription_value_usd: Mapped[Decimal | None] = mapped_column(_MONEY)


class Pricing(Base):
    """Date-versioned per-MTok prices for a provider model."""

    __tablename__ = "pricing"
    __table_args__ = (
        UniqueConstraint("provider", "model", "effective_date", name="pricing_grain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    effective_date: Mapped[date] = mapped_column(Date)
    input_per_mtok: Mapped[Decimal] = mapped_column(_MONEY)
    output_per_mtok: Mapped[Decimal] = mapped_column(_MONEY)
    cache_read_per_mtok: Mapped[Decimal] = mapped_column(_MONEY)
    cache_write_short_per_mtok: Mapped[Decimal] = mapped_column(_MONEY)
    cache_write_long_per_mtok: Mapped[Decimal] = mapped_column(_MONEY)
    source: Mapped[str] = mapped_column(String(50), default="litellm")


class RateCard(Base):
    """One generic per-unit price for a model, effective over a date range.

    The provider-neutral v2 pricing grain (D-006): a price is stored per single
    billable ``unit_type`` (``input_token``, ``output_token``, cache and future
    non-token units) rather than per MTok, so any provider's billing model fits
    one table. Resolution keys on provider, native model, unit type, timestamp,
    and the optional ``service_tier``/``mode``/``context_bracket`` dimensions
    with ``priority``/``override`` (task 64.3); overlapping conflicting rows are
    rejected in the service layer. ``unit_price`` uses exact ``Numeric(20,10)``.
    """

    __tablename__ = "rate_cards"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "native_model",
            "unit_type",
            "effective_from",
            "service_tier",
            "mode",
            "context_bracket",
            "priority",
            name="rate_cards_grain",
        ),
        Index(
            "ix_rate_cards_lookup",
            "provider",
            "native_model",
            "unit_type",
            "effective_from",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    native_model: Mapped[str] = mapped_column(String(200))
    unit_type: Mapped[str] = mapped_column(String(50))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    region: Mapped[str | None] = mapped_column(String(50))
    service_tier: Mapped[str | None] = mapped_column(String(50))
    mode: Mapped[str] = mapped_column(String(20), default="realtime")
    context_bracket: Mapped[str | None] = mapped_column(String(50))
    unit_price: Mapped[Decimal] = mapped_column(_MONEY)
    source: Mapped[str] = mapped_column(String(50), default="litellm")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    override: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Provider(Base):
    """Registry descriptor for one provider (lookup data, FR-PROVIDER-004).

    Seeded from the core provider descriptors and augmented by ingest when an
    unknown provider first appears (``registered`` marks whether it is a known
    seed or an observed unknown). This is reference data only: no usage row
    carries a foreign key into it (FR-MODEL-007), so registry edits never
    rewrite historical events.
    """

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(JSONType, default=list)
    pricing_strategy: Mapped[str] = mapped_column(String(50), default="")
    limit_semantics: Mapped[str] = mapped_column(String(50), default="none")
    supported_dimensions: Mapped[list[str]] = mapped_column(JSONType, default=list)
    registered: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Model(Base):
    """A provider model, seeded from the registry or observed in usage.

    Composite-keyed by ``(provider, native_model_id)`` -- the same grain the
    events carry. ``lifecycle`` is an enum-as-string
    (``active|deprecated|retired|unknown``, FR-MODEL-004) validated in the
    service layer, and ``capabilities`` is a free-form JSON map (FR-MODEL-005).
    Metadata updates here never touch historical events (FR-MODEL-007);
    ``last_seen`` is indexed for recency queries.
    """

    __tablename__ = "models"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    native_model_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    lifecycle: Mapped[str] = mapped_column(String(20), default="unknown")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ModelAlias(Base):
    """Maps a provider-specific model spelling to a canonical model id.

    Unique on ``(provider, alias)`` so one spelling resolves to exactly one
    model. ``rule_version`` records which normalization ruleset produced the
    mapping (FR-MODEL-009) so stale mappings can be recomputed after a rule
    change. No foreign key to ``models`` (registries are lookup data only).
    """

    __tablename__ = "model_aliases"
    __table_args__ = (
        UniqueConstraint("provider", "alias", name="model_aliases_grain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    alias: Mapped[str] = mapped_column(String(200))
    native_model_id: Mapped[str] = mapped_column(String(200))
    rule_version: Mapped[int] = mapped_column(Integer, default=1)


class DataQualityEvent(Base):
    """A recorded data-quality anomaly (unknown model, drift, skew, ...).

    A sink for ingest and pipeline anomalies that should surface in the UI and
    alerts without failing ingest. Bursts are collapsed by the recording
    service: one open (``resolved=False``) row per ``(kind, subject)`` within a
    configurable window, so a recurring issue is one row, not thousands.
    """

    __tablename__ = "data_quality_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_id: Mapped[str | None] = mapped_column(String(200))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertRule(Base):
    """A configurable alert condition and its delivery settings."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    # Legacy single threshold, kept for compatibility; warn/crit take priority.
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    # Dual thresholds: severity is derived from which one the measure crosses.
    warn_threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    crit_threshold: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    window_kind: Mapped[str | None] = mapped_column(String(50))
    # A JSON list of channel names (e.g. ["ntfy", "telegram"]).
    channels: Mapped[Any] = mapped_column(JSONType, default=list)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    quiet_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # Firing-state machine: "normal" or "firing"; drives resolved notices.
    state: Mapped[str] = mapped_column(String(20), default="normal")
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list[AlertEvent]] = relationship(back_populates="rule")


class AlertEvent(Base):
    """A fired alert instance, recording delivery outcome."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(String(2000))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    rule: Mapped[AlertRule] = relationship(back_populates="events")


class ApiToken(Base):
    """A hashed bearer token for API clients (dashboard, third-party apps)."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(200), unique=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Least-privilege scopes (task 63.3) and an optional source-name allowlist
    # that restricts which reporting sources the token may ingest for.
    scopes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    source_allowlist: Mapped[list[str] | None] = mapped_column(JSONType)


class AuditLog(Base):
    """An administrative action record (repricing, deletions, ...).

    Content-free operational history: who did what to which subject, with a JSON
    ``detail`` (filters, affected counts, versions). Repricing writes here now
    (task 64.6); Task 70 wires every administrative action to it.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100), index=True)
    subject: Mapped[str | None] = mapped_column(String(500))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AppSetting(Base):
    """A runtime key/value setting (e.g. UI-editable alert channel config).

    Values are stored as strings and coerced on read. Editable channel secrets
    live here so they can be changed from the UI; env settings remain the
    fallback when a key is absent or blank.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(2000), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
