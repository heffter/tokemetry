"""Response schemas for the v2 registry API.

Wire models for the provider and model registries. Kept separate from storage
so the API contract (FR-PROVIDER-010, FR-MODEL-010) evolves independently of
the ORM. All models forbid extra fields so response drift fails loudly in
tests.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tokemetry_server.api.serialization import UtcDatetime


class WindowOut(BaseModel):
    """One limit-window descriptor from the provider registry (FR-LIMIT-012)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    label: str
    period_kind: str
    period_seconds: int | None = None
    sort_order: int = 0


class ProviderOut(BaseModel):
    """Registry metadata for one provider (FR-PROVIDER-010)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    aliases: list[str]
    pricing_strategy: str
    limit_semantics: str
    supported_dimensions: list[str]
    windows: list[WindowOut]
    registered: bool


class ModelOut(BaseModel):
    """Registry metadata for one model, with its alias spellings (FR-MODEL-010).

    ``native_model_id`` is the provider's own id; ``aliases`` are the alternate
    spellings that normalize to it, so consumers see both the native and the
    normalized forms where both exist.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    native_model_id: str
    lifecycle: str
    capabilities: dict[str, Any]
    first_seen: UtcDatetime | None
    last_seen: UtcDatetime | None
    aliases: list[str]


class ValidationErrorItem(BaseModel):
    """One structured validation failure (FR-INGEST-006).

    ``index`` is the event's position in the batch (``-1`` for a batch-envelope
    error); ``field_path`` is a dotted path into the event; ``code`` and
    ``message`` name and describe the failure. This is the stable shape the
    generated clients (task 62.12) and the exporter conformance suite consume.
    """

    model_config = ConfigDict(extra="forbid")

    index: int
    field_path: str
    code: str
    message: str


class IngestEventsResponse(BaseModel):
    """Result of a successful ``POST /api/v2/ingest/events`` batch."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    request_id: str | None
    accepted: int
    updated: int
    duplicate: int
    rejected: int
    corrected: int
    accepted_ids: list[str] | None = None
    updated_ids: list[str] | None = None
    ids_truncated: bool = False


class ValidateResponse(BaseModel):
    """Result of ``POST /api/v2/ingest/validate`` (never persists)."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    request_id: str | None
    errors: list[ValidationErrorItem]


class MetaIngestResponse(BaseModel):
    """Result of the v2 limits and aggregates ingest endpoints."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    request_id: str | None
    accepted: int


class SourceHealthOut(BaseModel):
    """Query-time health of a reporting source (FR-SOURCE-005)."""

    model_config = ConfigDict(extra="forbid")

    stale: bool
    last_successful_ingest: UtcDatetime | None
    recent_error_count: int
    reported_schema_version: int | None
    clock_skew_seconds: float | None
    staleness_threshold_seconds: float


class SourceOut(BaseModel):
    """A reporting source with its health (FR-SOURCE-001..006). No secrets."""

    model_config = ConfigDict(extra="forbid")

    id: int
    type: str
    name: str
    version: str | None
    instance_id: str | None
    machine: str | None
    token_label: str | None
    billing_mode: str
    first_seen: UtcDatetime
    last_seen: UtcDatetime
    revoked: bool
    health: SourceHealthOut


class SourceUpdateRequest(BaseModel):
    """Mutable source fields (label and billing mode); event identity is fixed."""

    model_config = ConfigDict(extra="forbid")

    token_label: str | None = None
    billing_mode: str | None = None


class RepriceRequest(BaseModel):
    """Reprice a time range's costs under a new pricing version."""

    model_config = ConfigDict(extra="forbid")

    start: UtcDatetime
    end: UtcDatetime
    provider: str | None = None
    native_model: str | None = None


class RevertRequest(BaseModel):
    """Re-activate a named prior pricing version for a time range."""

    model_config = ConfigDict(extra="forbid")

    pricing_version: str
    start: UtcDatetime
    end: UtcDatetime
    provider: str | None = None
    native_model: str | None = None


class RepriceResponse(BaseModel):
    """The outcome of a reprice or revert operation."""

    model_config = ConfigDict(extra="forbid")

    pricing_version: str
    affected: int


class ImportRequest(BaseModel):
    """Apply a rate-card import; ``digest`` is required to apply a dry run."""

    model_config = ConfigDict(extra="forbid")

    #: The digest returned by the dry run; required when ``dry_run=false``.
    digest: str | None = None


class ImportChangeOut(BaseModel):
    """One row's effect in an import diff (new/superseded/unchanged/conflict)."""

    model_config = ConfigDict(extra="forbid")

    action: str
    provider: str
    native_model: str
    unit_type: str
    priority: int
    new_price: Decimal | None = None


class ImportResponse(BaseModel):
    """A rate-card import dry-run diff or apply result (D-015)."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    digest: str
    new: int
    superseded: int
    unchanged: int
    conflicts: int
    changes: list[ImportChangeOut]


class RateCardOut(BaseModel):
    """One stored rate card (the v2 per-unit pricing grain)."""

    model_config = ConfigDict(extra="forbid")

    id: int
    provider: str
    native_model: str
    unit_type: str
    effective_from: date
    effective_to: date | None
    currency: str
    region: str | None
    service_tier: str | None
    mode: str
    context_bracket: str | None
    unit_price: Decimal
    source: str
    priority: int
    override: bool


class RateCardCreateRequest(BaseModel):
    """Create a rate card (manual price or override)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    native_model: str
    unit_type: str
    effective_from: date
    unit_price: Decimal
    currency: str = "USD"
    mode: str = "realtime"
    service_tier: str | None = None
    context_bracket: str | None = None
    region: str | None = None
    source: str = "manual"
    priority: int = 0
    override: bool = False
    effective_to: date | None = None


class RateCardMutationResponse(BaseModel):
    """A created rate card plus the current pricing-state version."""

    model_config = ConfigDict(extra="forbid")

    rate_card: RateCardOut
    pricing_version: str


class RateCardCloseRequest(BaseModel):
    """Close a rate card by setting its effective_to date."""

    model_config = ConfigDict(extra="forbid")

    effective_to: date


class RateCardCloseResponse(BaseModel):
    """The outcome of closing a rate card."""

    model_config = ConfigDict(extra="forbid")

    rate_card_id: int
    pricing_version: str


class UnpricedReportRow(BaseModel):
    """An aggregate of unpriced or partially priced events for one model."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    native_model: str
    cost_status: str
    event_count: int


class UnknownModelReportRow(BaseModel):
    """An unknown-model observation recorded at ingest."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    native_model: str
    observations: int
    resolved: bool
    last_seen: UtcDatetime


class QueryWarningOut(BaseModel):
    """A data-quality caveat attached to a v2 query response (FR-QUERY-010)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    detail: str
    count: int


class PageMeta(BaseModel):
    """Keyset-pagination metadata for a v2 list response (FR-QUERY-003)."""

    model_config = ConfigDict(extra="forbid")

    #: Opaque cursor for the next page, or null on the last page.
    next_cursor: str | None = None


class UsageRowOut(BaseModel):
    """One grouped usage aggregate (six counters plus attempt count)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    reasoning_tokens: int
    total_tokens: int
    attempt_count: int


class UsageResponse(BaseModel):
    """Grouped final-attempt usage with data-quality warnings (FR-QUERY-007)."""

    model_config = ConfigDict(extra="forbid")

    group_by: str
    rows: list[UsageRowOut]
    warnings: list[QueryWarningOut]


class CostRowOut(BaseModel):
    """One grouped cost aggregate: dual metrics never merged (FR-COST-012)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    actual_spend_usd: Decimal
    subscription_value_usd: Decimal
    cost_priced_usd: Decimal
    cost_partial_usd: Decimal
    cost_estimated_usd: Decimal
    unpriced_event_count: int
    pricing_version: str


class CostResponse(BaseModel):
    """Grouped costs with data-quality warnings (FR-QUERY-006)."""

    model_config = ConfigDict(extra="forbid")

    group_by: str
    rows: list[CostRowOut]
    warnings: list[QueryWarningOut]


class ReconciliationRowOut(BaseModel):
    """Observed-versus-computed cost drift for a provider (and optional day)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    computed_usd: Decimal
    observed_usd: Decimal
    drift_usd: Decimal
    event_count: int
    day: str | None = None
    drift_pct: Decimal | None = None


class ReconciliationResponse(BaseModel):
    """Cost reconciliation drift rows."""

    model_config = ConfigDict(extra="forbid")

    rows: list[ReconciliationRowOut]


class AttemptOut(BaseModel):
    """One final attempt event with lifecycle and usage fields (FR-QUERY-002)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    provider: str
    native_model: str
    requested_model: str | None
    routed_model: str | None
    ts_started: UtcDatetime
    ts_completed: UtcDatetime | None
    latency_ms: int | None
    success: bool
    logical_request_id: str | None
    session_id: str | None
    source: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    reasoning_tokens: int
    cost_usd: Decimal | None


class AttemptsResponse(BaseModel):
    """A keyset-paginated page of attempt events."""

    model_config = ConfigDict(extra="forbid")

    attempts: list[AttemptOut]
    next_cursor: str | None = None


class RequestOut(BaseModel):
    """A logical request with its attempt-chain aggregates (FR-TRACE-007/012)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    logical_request_id: str
    requested_model: str | None
    session_id: str | None
    routing_policy: str | None
    routing_reason: str | None
    attempt_count: int
    fallback_count: int
    winning_attempt_id: str | None
    ts_first: UtcDatetime | None
    ts_last: UtcDatetime | None
    total_tokens: int
    cost_usd: Decimal | None


class RequestsResponse(BaseModel):
    """A keyset-paginated page of logical requests."""

    model_config = ConfigDict(extra="forbid")

    requests: list[RequestOut]
    next_cursor: str | None = None


class RequestDetailResponse(BaseModel):
    """A logical request with its ordered attempts (fallback-chain drilldown)."""

    model_config = ConfigDict(extra="forbid")

    request: RequestOut
    attempts: list[AttemptOut]


class SessionOut(BaseModel):
    """A session rollup keyed by scoped identity (FR-TRACE-010/011)."""

    model_config = ConfigDict(extra="forbid")

    scoped_id: str
    provider: str
    source: str
    session_id: str
    attempt_count: int
    total_tokens: int
    cost_usd: Decimal | None
    ts_first: UtcDatetime | None
    ts_last: UtcDatetime | None


class SessionsResponse(BaseModel):
    """A paginated page of session rollups."""

    model_config = ConfigDict(extra="forbid")

    sessions: list[SessionOut]
    next_cursor: str | None = None


class LimitSnapshotOut(BaseModel):
    """One limit-utilization snapshot with its provenance (FR-LIMIT-004).

    The v2 dimensions (account/organization/source_id) and measures
    (limit_amount/remaining/unit) are null for v1 and dimension-less snapshots.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    provider: str
    machine: str | None
    ts: UtcDatetime
    window_kind: str
    utilization_pct: Decimal
    resets_at: UtcDatetime | None
    provenance: str
    account: str | None = None
    organization: str | None = None
    source_id: int | None = None
    limit_amount: Decimal | None = None
    remaining: Decimal | None = None
    unit: str | None = None


class LimitsResponse(BaseModel):
    """A keyset-paginated page of limit snapshots."""

    model_config = ConfigDict(extra="forbid")

    limits: list[LimitSnapshotOut]
    next_cursor: str | None = None


class LimitStreamOut(BaseModel):
    """The stream a forecast was computed from (its source data, FR-LIMIT-008)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    window_kind: str
    account: str | None
    organization: str | None
    source_id: int | None


class LimitForecastOut(BaseModel):
    """An exhaustion forecast for one limit stream with confidence (FR-LIMIT-008)."""

    model_config = ConfigDict(extra="forbid")

    stream: LimitStreamOut
    utilization_pct: float
    slope_pct_per_min: float
    predicted_exhaustion_at: UtcDatetime | None
    resets_at: UtcDatetime | None
    will_reset_first: bool
    sample_count: int
    #: ``high`` | ``medium`` | ``low`` | ``unavailable``.
    confidence: str


class LimitForecastResponse(BaseModel):
    """Per-stream limit exhaustion forecasts over a range."""

    model_config = ConfigDict(extra="forbid")

    forecasts: list[LimitForecastOut]


class DataQualityEventOut(BaseModel):
    """One recorded data-quality anomaly."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    kind: str
    subject: str
    detail: dict[str, Any]
    source_id: str | None
    ts: UtcDatetime
    resolved: bool


class DataQualityResponse(BaseModel):
    """A keyset-paginated page of data-quality events."""

    model_config = ConfigDict(extra="forbid")

    events: list[DataQualityEventOut]
    next_cursor: str | None = None


class RollupOut(BaseModel):
    """One daily_rollups row exposed for external tooling (stable columns)."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    day: date
    provider: str
    model: str
    machine: str
    project: str
    source: str
    environment: str
    billing_mode: str
    provenance: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_short_tokens: int
    cache_write_long_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost_usd: Decimal | None
    cost_priced_usd: Decimal | None
    cost_partial_usd: Decimal | None
    cost_estimated_usd: Decimal | None
    unpriced_event_count: int
    subscription_value_usd: Decimal | None


class RollupsResponse(BaseModel):
    """A keyset-paginated page of daily rollup rows."""

    model_config = ConfigDict(extra="forbid")

    rollups: list[RollupOut]
    next_cursor: str | None = None


class RetentionCategoryConfig(BaseModel):
    """Retention rule for one record category (Task 70.1).

    ``retention_days`` is a positive whole-day count, or ``null`` for an
    indefinite (never-deleted) category. ``enabled`` gates deletion of the
    category independently of the duration.
    """

    model_config = ConfigDict(extra="forbid")

    retention_days: int | None = Field(default=None, ge=1)
    enabled: bool


class RetentionPolicyBody(BaseModel):
    """The full per-category retention policy plus the global legal hold.

    Used for both the GET response and the PUT request: the client reads the
    resolved policy, edits it, and writes it back in full. ``categories`` must
    name every known category exactly.
    """

    model_config = ConfigDict(extra="forbid")

    categories: dict[str, RetentionCategoryConfig]
    legal_hold: bool


class RetentionCategoryStatus(BaseModel):
    """One category's policy plus its last retention-worker outcome (Task 70.2)."""

    model_config = ConfigDict(extra="forbid")

    category: str
    retention_days: int | None
    enabled: bool
    last_run_at: UtcDatetime | None
    last_deleted: int
    total_deleted: int
    pending_backlog: int
    oldest_retained: UtcDatetime | None


class RetentionStatusResponse(BaseModel):
    """Operational retention status across every category (FR-RET-005)."""

    model_config = ConfigDict(extra="forbid")

    legal_hold: bool
    categories: list[RetentionCategoryStatus]


class DeletionCriteriaBody(BaseModel):
    """Targeted deletion criteria; any combination narrows the match (Task 70.3)."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    machine: str | None = None
    project: str | None = None
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None


class DeletionRequest(BaseModel):
    """A dry-run or confirm administrative-deletion request."""

    model_config = ConfigDict(extra="forbid")

    criteria: DeletionCriteriaBody
    #: Required only on confirm (``dry_run=false``): the dry run's digest.
    digest: str | None = None
    #: Recompute the affected days' rollups after deletion (default true).
    recompute_rollups: bool = True


class DeletionResponse(BaseModel):
    """The dry-run preview or the confirmed deletion outcome."""

    model_config = ConfigDict(extra="forbid")

    executed: bool
    counts: dict[str, int]
    affected_days: list[str]
    digest: str
    rollups_recomputed: int


class AuditEntryOut(BaseModel):
    """One append-only audit entry for review (Task 70.4)."""

    model_config = ConfigDict(extra="forbid")

    id: int
    ts: UtcDatetime
    actor: str | None
    action: str
    subject: str | None
    detail: dict[str, Any]
    request_id: str | None
