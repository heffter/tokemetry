"""Response schemas for the v2 registry API.

Wire models for the provider and model registries. Kept separate from storage
so the API contract (FR-PROVIDER-010, FR-MODEL-010) evolves independently of
the ORM. All models forbid extra fields so response drift fails loudly in
tests.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from tokemetry_server.api.serialization import UtcDatetime


class ProviderOut(BaseModel):
    """Registry metadata for one provider (FR-PROVIDER-010)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    aliases: list[str]
    pricing_strategy: str
    limit_semantics: str
    supported_dimensions: list[str]
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
