"""Parse an OTLP/HTTP JSON trace export into spans (Task 71.3).

Decodes the OTLP ``ExportTraceServiceRequest`` JSON encoding (the dependency-free
OTLP/HTTP transport) into :class:`~tokemetry_server.otel.convert.OtelSpan`
objects for the GenAI converter. A protobuf body decoder can slot in behind the
same :func:`parse_otlp_json` boundary without changing the converter or the
endpoint.

OTLP/JSON encodes attribute values as typed objects (``stringValue``,
``intValue``, ...) and trace/span ids as hex strings, which is exactly the W3C
trace-context form the ledger validates.
"""

from __future__ import annotations

from typing import Any

from tokemetry_server.otel.convert import OtelSpan


class OtlpParseError(ValueError):
    """The OTLP payload was not a well-formed trace export."""


def _attr_value(value: dict[str, Any]) -> Any:
    """Unwrap one OTLP typed attribute value to a Python scalar."""
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    # arrayValue / kvlistValue and unknown shapes are dropped -- the GenAI
    # mapping only reads scalars, and complex values are not needed here.
    return None


def _attributes(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten an OTLP attribute list into a ``{key: scalar}`` dict."""
    attrs: dict[str, Any] = {}
    for item in raw:
        key = item.get("key")
        value = item.get("value")
        if isinstance(key, str) and isinstance(value, dict):
            scalar = _attr_value(value)
            if scalar is not None:
                attrs[key] = scalar
    return attrs


def _span(raw: dict[str, Any], scope_name: str | None) -> OtelSpan:
    span_id = raw.get("spanId")
    start = raw.get("startTimeUnixNano")
    if not isinstance(span_id, str) or start is None:
        raise OtlpParseError("span missing spanId or startTimeUnixNano")
    status = raw.get("status") or {}
    end = raw.get("endTimeUnixNano")
    parent = raw.get("parentSpanId") or None
    return OtelSpan(
        span_id=span_id,
        name=str(raw.get("name", "")),
        start_unix_nano=int(start),
        end_unix_nano=int(end) if end is not None else None,
        attributes=_attributes(raw.get("attributes") or []),
        trace_id=raw.get("traceId") or None,
        parent_span_id=parent if isinstance(parent, str) else None,
        status_code=status.get("code"),
        status_message=status.get("message"),
        scope_name=scope_name,
    )


def parse_otlp_json(payload: dict[str, Any]) -> list[OtelSpan]:
    """Parse an OTLP/HTTP JSON trace export into spans.

    Raises:
        OtlpParseError: If the top-level shape is not a trace export.
    """
    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list):
        raise OtlpParseError("payload has no resourceSpans list")
    spans: list[OtelSpan] = []
    for resource in resource_spans:
        for scope in resource.get("scopeSpans", []) or []:
            scope_name = (scope.get("scope") or {}).get("name")
            for raw in scope.get("spans", []) or []:
                spans.append(_span(raw, scope_name))
    return spans
