"""Response schemas for the query API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class UsageBucketOut(BaseModel):
    """Aggregated usage for one group-by key."""

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    total_tokens: int
    cost_usd: Decimal | None


class UsageResponse(BaseModel):
    """Grouped usage over a range."""

    group_by: str
    start: date
    end: date
    buckets: list[UsageBucketOut]


class LimitOut(BaseModel):
    """Current or historical limit utilization."""

    provider: str
    window_kind: str
    utilization_pct: float
    resets_at: datetime | None
    ts: datetime
    provenance: str


class PredictionOut(BaseModel):
    """Extrapolated exhaustion for a limit window."""

    window_kind: str
    utilization_pct: float
    slope_pct_per_min: float
    predicted_exhaustion_at: datetime | None
    resets_at: datetime | None


class TodaySummary(BaseModel):
    """Today's totals and per-model breakdown."""

    total_tokens: int
    cost_usd: Decimal | None
    by_model: list[UsageBucketOut]


class SummaryNow(BaseModel):
    """The dashboard front-page summary."""

    now: datetime
    limits: list[LimitOut]
    token_burn_rate_per_min: float
    prediction: PredictionOut | None
    today: TodaySummary


class BlockOut(BaseModel):
    """One reconstructed 5-hour usage block."""

    start: datetime
    end: datetime
    total_tokens: int
    cost_usd: Decimal | None
    peak_tokens_per_min: int
    end_utilization_pct: float | None


class SessionOut(BaseModel):
    """Aggregated session summary."""

    session_id: str
    provider: str
    machine: str | None
    project: str | None
    started_at: datetime
    last_at: datetime
    message_count: int
    total_tokens: int
    cost_usd: Decimal | None


class MachineOut(BaseModel):
    """Fleet-view machine summary."""

    id: str
    platform: str | None
    last_seen: datetime | None
    collector_version: str | None
    total_tokens: int
    event_count: int


class PunchCell(BaseModel):
    """One weekday/hour cell of the punch card."""

    weekday: int
    hour: int
    total_tokens: int


class HeatmapResponse(BaseModel):
    """Calendar (daily) and punch-card (weekday x hour) usage."""

    calendar: list[UsageBucketOut]
    punch_card: list[PunchCell]


class CostResponse(BaseModel):
    """Cost over a range with the subscription value multiple."""

    start: date
    end: date
    total_cost_usd: Decimal
    subscription_monthly_usd: float | None
    value_multiple: float | None


class PricingOut(BaseModel):
    """A pricing table row."""

    provider: str
    model: str
    effective_date: date
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_read_per_mtok: Decimal
    cache_write_short_per_mtok: Decimal
    cache_write_long_per_mtok: Decimal
    source: str


class PriceRowIn(BaseModel):
    """A price row to create or override."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=200)
    effective_date: date
    input_per_mtok: Decimal = Field(ge=0)
    output_per_mtok: Decimal = Field(ge=0)
    cache_read_per_mtok: Decimal = Field(ge=0)
    cache_write_short_per_mtok: Decimal = Field(ge=0)
    cache_write_long_per_mtok: Decimal = Field(ge=0)
    source: str = Field(default="manual", max_length=50)


class RecomputeResult(BaseModel):
    """Outcome of a cost recomputation."""

    events_updated: int
    rollups_refreshed: int


class SyncResult(BaseModel):
    """Outcome of a LiteLLM price sync."""

    synced: int


class TokenCreateRequest(BaseModel):
    """Request to mint an API token."""

    label: str


class TokenCreatedOut(BaseModel):
    """A newly created token; the secret appears only here."""

    label: str
    token: str
    created_at: datetime


class TokenInfoOut(BaseModel):
    """Stored token metadata (never the secret)."""

    label: str
    created_at: datetime
    last_used: datetime | None
    revoked: bool
