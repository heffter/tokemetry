"""Response schemas for the v2 registry API.

Wire models for the provider and model registries. Kept separate from storage
so the API contract (FR-PROVIDER-010, FR-MODEL-010) evolves independently of
the ORM. All models forbid extra fields so response drift fails loudly in
tests.
"""

from __future__ import annotations

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
