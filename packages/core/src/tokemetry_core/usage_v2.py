"""Provider-neutral v2 usage event wire model (PRD-TOK-002 Section 12.3).

The v2 event carries the full attempt lifecycle the v1 :class:`UsageEvent`
cannot express: finality and increasing sequence for streamed snapshots
(FR-EVENT-005/006), separate requested/routed/native model identities
(FR-EVENT-012), reasoning tokens kept apart from visible output
(FR-EVENT-015), success and outcome as distinct terminal signals
(FR-EVENT-017), source identity (FR-EVENT-018), gateway-neutral routing
(FR-EVENT-019), bounded dimensions (FR-EVENT-020), and OpenTelemetry trace
linkage (FR-OTEL-001).

The schema deliberately has no content fields (FR-EVENT-021): no prompts,
completions, tool arguments, file paths, or reasoning text can be represented.
This module defines the wire shape and per-field invariants only; the privacy
validation layer (task 62.2) adds prohibited-key rejection and dimension
bounds. ``frozen=True`` keeps ingested events immutable through the pipeline
and ``extra=forbid`` makes wire drift fail loudly in tests.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from tokemetry_core.models import Provenance, _FrozenModel, _require_tz

#: The wire schema version every v2 event must declare (FR-EVENT-001).
SCHEMA_VERSION = 2


class EventKind(enum.StrEnum):
    """What a v2 event record represents (FR-EVENT-003).

    ATTEMPT: one upstream provider attempt -- the only billable kind
        (FR-EVENT-004).
    LOGICAL_REQUEST: a summary grouping the attempts of one logical request.
    IMPORT: a record brought in from an external dataset or backfill.
    ADJUSTMENT: an administrative correction to earlier usage.
    """

    ATTEMPT = "attempt"
    LOGICAL_REQUEST = "logical_request"
    IMPORT = "import"
    ADJUSTMENT = "adjustment"


class Finality(enum.StrEnum):
    """Whether an event is an in-progress snapshot or a terminal state.

    SNAPSHOT: a partial, still-changing view (for example a streamed response
        mid-flight); superseded by a higher sequence or a final event.
    FINAL: the terminal state; supersedes any snapshot and only changes again
        through an explicit correction (FR-IDEMP-004/005).
    """

    SNAPSHOT = "snapshot"
    FINAL = "final"


class SourceType(enum.StrEnum):
    """The kind of reporting source a v2 event came from (FR-SOURCE-002)."""

    COLLECTOR = "collector"
    GATEWAY = "gateway"
    SDK = "sdk"
    IMPORTER = "importer"
    MANUAL = "manual"


class SourceRef(_FrozenModel):
    """Identity of the source that reported an event (FR-EVENT-018).

    Source identity is distinct from machine identity (FR-SOURCE-003): one
    machine may host several sources. ``version`` is required so source health
    can track schema/version drift; ``instance_id`` distinguishes concurrent
    instances of the same named source and is optional.
    """

    type: SourceType
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    instance_id: str | None = None


class Routing(_FrozenModel):
    """Gateway-neutral routing metadata for one attempt (FR-EVENT-019).

    Optional and provider-agnostic: a gateway that cascades across models
    records the policy that chose a model, why it was chosen, this attempt's
    index in the chain, and -- for a fallback -- which model it fell back from
    and what triggered the fallback. Gateway-specific detail beyond these
    fields belongs in ``UsageEventV2.extra`` under a namespaced key.
    """

    policy: str | None = None
    reason: str | None = None
    attempt_index: int | None = Field(default=None, ge=0)
    fallback_from: str | None = None
    fallback_trigger: str | None = None


class UsageEventV2(_FrozenModel):
    """One provider-neutral v2 usage event (PRD Section 12.3 wire shape).

    ``event_id`` is unique within the canonical provider namespace
    (FR-EVENT-002); ingest resolves revisions of the same id by ``finality``
    and ``sequence``. Token counters are non-negative and default to zero so a
    failed or cancelled attempt is ingestible with no usage (FR-EVENT-024).
    ``reasoning_tokens`` is stored apart from ``output_tokens`` (FR-EVENT-015),
    and ``success`` (did the attempt succeed) is kept separate from ``outcome``
    (the nuanced terminal state) per FR-EVENT-017.
    """

    schema_version: Literal[2]
    event_id: str = Field(min_length=1)
    event_kind: EventKind
    finality: Finality
    sequence: int = Field(ge=0)

    logical_request_id: str | None = None
    attempt_id: str | None = None
    provider_request_id: str | None = None
    provider_response_id: str | None = None

    provider: str = Field(min_length=1)
    native_model: str = Field(min_length=1)
    requested_model: str | None = None
    routed_model: str | None = None

    ts_started: datetime
    ts_first_token: datetime | None = None
    ts_completed: datetime | None = None

    machine: str | None = None
    project: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    environment: str | None = None

    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cache_read_tokens: int = Field(ge=0, default=0)
    cache_write_short_tokens: int = Field(ge=0, default=0)
    cache_write_long_tokens: int = Field(ge=0, default=0)
    reasoning_tokens: int = Field(ge=0, default=0)

    success: bool = False
    outcome: str | None = None
    http_status: int | None = None
    stop_reason: str | None = None
    service_tier: str | None = None
    streaming: bool | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    time_to_first_token_ms: int | None = Field(default=None, ge=0)
    tool_call_count: int = Field(ge=0, default=0)
    tool_histogram: dict[str, int] | None = None

    provenance: Provenance = Provenance.LOCAL_ESTIMATE
    source: SourceRef

    routing: Routing | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    @field_validator("ts_started")
    @classmethod
    def _validate_ts_started(cls, value: datetime) -> datetime:
        """The start timestamp must be timezone-aware (FR-EVENT-013)."""
        return _require_tz(value)

    @field_validator("ts_first_token", "ts_completed")
    @classmethod
    def _validate_optional_ts(cls, value: datetime | None) -> datetime | None:
        """First-token and completion timestamps, when present, are tz-aware."""
        if value is None:
            return None
        return _require_tz(value)

    @property
    def total_tokens(self) -> int:
        """Sum of every token category, including reasoning."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_short_tokens
            + self.cache_write_long_tokens
            + self.reasoning_tokens
        )


def usage_event_json_schema() -> dict[str, Any]:
    """Return the published JSON schema for the v2 usage event (FR-INGEST-012).

    Generated from :class:`UsageEventV2`, so the served schema and the model
    validated at ingest can never drift. The ``GET /api/v2/schemas/usage-event``
    endpoint (task 62.12) serves exactly this document.
    """
    schema = UsageEventV2.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "UsageEventV2"
    schema["x-tokemetry-schema-version"] = SCHEMA_VERSION
    return schema
