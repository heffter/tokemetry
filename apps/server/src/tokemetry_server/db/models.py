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
    ForeignKey,
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

    Absent dimensions (``machine``, ``project``) use ``''`` sentinels so the
    unique grain constraint holds identically on SQLite and Postgres.
    """

    __tablename__ = "daily_rollups"
    __table_args__ = (
        UniqueConstraint(
            "day", "provider", "machine", "model", "project", name="daily_rollups_grain"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    provider: Mapped[str] = mapped_column(String(50))
    machine: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    project: Mapped[str] = mapped_column(String(500), default="")
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_short_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_long_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(_MONEY)
    provenance: Mapped[str] = mapped_column(String(30), default="derived")


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
