"""Schemas for alert rule CRUD and alert history."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tokemetry_server.api.serialization import UtcDatetime


class AlertFiltersIn(BaseModel):
    """Optional dimension filters scoping a rule's evaluation (Task 68.1).

    Each list is a set of allowed values for that dimension; an absent or empty
    list leaves the dimension unscoped. Unknown keys are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    provider: list[str] = Field(default_factory=list)
    model: list[str] = Field(default_factory=list)
    source: list[str] = Field(default_factory=list)
    project: list[str] = Field(default_factory=list)
    environment: list[str] = Field(default_factory=list)


class AlertConfigIn(BaseModel):
    """An alert rule's config object: dimension filters and window settings.

    ``window_minutes`` and ``min_samples`` tune the sliding-window reliability
    kinds (``failure_rate``, ``latency_p95``, ``fallback_rate``); other kinds
    ignore them. Both are optional and fall back to per-kind defaults.
    """

    model_config = ConfigDict(extra="forbid")

    filters: AlertFiltersIn = Field(default_factory=AlertFiltersIn)
    window_minutes: int | None = Field(default=None, ge=1)
    min_samples: int | None = Field(default=None, ge=1)


class AlertRuleIn(BaseModel):
    """Create/update payload for an alert rule."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=50)
    threshold: Decimal | None = None
    warn_threshold: Decimal | None = None
    crit_threshold: Decimal | None = None
    window_kind: str | None = Field(default=None, max_length=50)
    channels: list[str] = Field(default_factory=list)
    cooldown_seconds: int = Field(default=3600, ge=0)
    quiet_hours: dict[str, Any] | None = None
    enabled: bool = True
    config: AlertConfigIn = Field(default_factory=AlertConfigIn)


class AlertRuleOut(BaseModel):
    """An alert rule as returned by the API."""

    id: int
    name: str
    kind: str
    threshold: Decimal | None
    warn_threshold: Decimal | None
    crit_threshold: Decimal | None
    window_kind: str | None
    channels: list[str]
    cooldown_seconds: int
    quiet_hours: dict[str, Any] | None
    enabled: bool
    config: dict[str, Any]
    state: str
    last_fired_at: UtcDatetime | None


class AlertEventOut(BaseModel):
    """A fired alert instance."""

    id: int
    rule_id: int
    ts: UtcDatetime
    severity: str
    title: str
    body: str
    delivered: bool
    context: dict[str, Any]


class EvaluateResult(BaseModel):
    """Summary of a manual evaluation run."""

    fired: list[AlertEventOut]


class TestChannelResult(BaseModel):
    """Outcome of a test notification to one channel."""

    channel: str
    delivered: bool


class ChannelFieldOut(BaseModel):
    """One channel config field as shown in the UI (secrets masked)."""

    name: str
    value: str
    is_secret: bool
    is_set: bool


class ChannelOut(BaseModel):
    """A notification channel's configured state and (masked) fields."""

    name: str
    configured: bool
    fields: list[ChannelFieldOut]


class ChannelsResponse(BaseModel):
    """All notification channels' current configuration."""

    channels: list[ChannelOut]


class ChannelConfigIn(BaseModel):
    """Editable channel fields; absent field = unchanged, "" = clear to env."""

    model_config = ConfigDict(extra="forbid")

    ntfy_url: str | None = None
    ntfy_topic: str | None = None
    dashboard_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    smtp_use_tls: str | None = None
