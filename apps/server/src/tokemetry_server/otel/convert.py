"""Convert OpenTelemetry GenAI spans to v2 attempt events (Tasks 71.3/71.4).

Implements the pinned mapping from :mod:`tokemetry_server.otel.semconv`. Content
attributes are stripped unconditionally before an event is built (FR-OTEL-007);
unmapped attributes are retained under ``extra.otel`` within the standard
metadata bounds; the pinned semconv version is recorded on every event
(FR-OTEL-006). A span with no ``gen_ai.system`` is not a GenAI span and yields
``None`` (it is ignored, not an error).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2

from tokemetry_server.otel.semconv import (
    ERROR_TYPE_ATTR,
    EXTRA_NAMESPACE,
    OPERATION_ATTR,
    PROVIDER_ATTR,
    REQUESTED_MODEL_ATTR,
    RESPONSE_MODEL_ATTR,
    SEMCONV_VERSION,
    SPAN_DERIVED_DEFAULTS,
    TOKEN_ATTR_TO_FIELD,
    is_content_attr,
)

#: Attributes consumed by explicit mapping, so they are not duplicated into
#: ``extra.otel`` as leftovers.
_MAPPED_ATTRS: frozenset[str] = frozenset(
    {PROVIDER_ATTR, REQUESTED_MODEL_ATTR, RESPONSE_MODEL_ATTR, ERROR_TYPE_ATTR}
    | set(TOKEN_ATTR_TO_FIELD)
)


@dataclass(frozen=True)
class OtelSpan:
    """A minimal parsed OTLP span (the fields the GenAI mapping needs)."""

    span_id: str
    name: str
    start_unix_nano: int
    attributes: dict[str, Any]
    end_unix_nano: int | None = None
    trace_id: str | None = None
    parent_span_id: str | None = None
    status_code: str | None = None  # e.g. "STATUS_CODE_ERROR"
    status_message: str | None = None
    scope_name: str | None = None


def _ts(unix_nano: int | None) -> datetime | None:
    if unix_nano is None:
        return None
    return datetime.fromtimestamp(unix_nano / 1_000_000_000, tz=UTC)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extra(span: OtelSpan, clean_attrs: dict[str, Any]) -> dict[str, Any]:
    """Build the ``extra.otel`` namespace: version, operation, and leftovers."""
    leftovers = {
        key: value
        for key, value in clean_attrs.items()
        if key not in _MAPPED_ATTRS
    }
    otel: dict[str, Any] = {
        "semconv_version": SEMCONV_VERSION,
        "attributes": leftovers,
    }
    if PROVIDER_ATTR in clean_attrs:
        otel["system"] = clean_attrs[PROVIDER_ATTR]
    if OPERATION_ATTR in clean_attrs:
        otel["operation"] = clean_attrs[OPERATION_ATTR]
    return {EXTRA_NAMESPACE: otel}


def _success_outcome(span: OtelSpan, clean_attrs: dict[str, Any]) -> tuple[bool, str]:
    """Derive (success, outcome) from span status and error.type."""
    errored = span.status_code == "STATUS_CODE_ERROR" or ERROR_TYPE_ATTR in clean_attrs
    return (not errored), ("error" if errored else "success")


def span_to_event(span: OtelSpan, *, source_name: str = "otel") -> UsageEventV2 | None:
    """Map a GenAI span to a v2 attempt event, or ``None`` if not GenAI."""
    clean_attrs = {
        key: value
        for key, value in span.attributes.items()
        if not is_content_attr(key)
    }
    system = clean_attrs.get(PROVIDER_ATTR)
    if not system:
        return None  # not a GenAI span

    provider = str(system).strip().lower()
    native_model = (
        clean_attrs.get(RESPONSE_MODEL_ATTR)
        or clean_attrs.get(REQUESTED_MODEL_ATTR)
        or "unknown"
    )
    tokens = {
        field_name: _int(clean_attrs[attr])
        for attr, field_name in TOKEN_ATTR_TO_FIELD.items()
        if attr in clean_attrs
    }
    success, outcome = _success_outcome(span, clean_attrs)
    ts_completed = _ts(span.end_unix_nano)
    ts_started = _ts(span.start_unix_nano)
    latency_ms = None
    if span.end_unix_nano is not None:
        latency_ms = max(0, (span.end_unix_nano - span.start_unix_nano) // 1_000_000)

    return UsageEventV2.model_validate(
        {
            "schema_version": 2,
            "event_id": span.span_id,
            "provider": provider,
            "native_model": str(native_model),
            "requested_model": clean_attrs.get(REQUESTED_MODEL_ATTR),
            "ts_started": ts_started,
            "ts_completed": ts_completed,
            "latency_ms": latency_ms,
            "success": success,
            "outcome": outcome,
            "provenance": "imported",
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "extra": _extra(span, clean_attrs),
            "source": SourceRef(
                type=SourceType.SDK, name=source_name, version=SEMCONV_VERSION
            ),
            **SPAN_DERIVED_DEFAULTS,
            **tokens,
        }
    )


def spans_to_events(
    spans: list[OtelSpan], *, source_name: str = "otel"
) -> list[UsageEventV2]:
    """Convert every GenAI span in ``spans`` (ignoring non-GenAI ones)."""
    events: list[UsageEventV2] = []
    for span in spans:
        event = span_to_event(span, source_name=source_name)
        if event is not None:
            events.append(event)
    return events
