"""Schemas for alert rule CRUD and alert history."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tokemetry_server.api.serialization import UtcDatetime


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
    config: dict[str, Any] = Field(default_factory=dict)


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
