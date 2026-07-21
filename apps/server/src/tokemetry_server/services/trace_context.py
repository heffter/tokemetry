"""W3C trace-context validation for v2 events (Task 71.1, FR-OTEL-001).

Validates ``trace_id`` / ``span_id`` / ``parent_span_id`` against the W3C
trace-context formats (a 16-byte trace id and 8-byte span ids, lowercase hex,
non-zero). Validation is **lenient**: a non-conforming id is passed through and
stored as-is so no telemetry is dropped, but the malformed fields are reported
so ingest can record a data-quality note (D-013 pins the exact convention in the
mapping document).
"""

from __future__ import annotations

import re

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_ZERO_TRACE_ID = "0" * 32
_ZERO_SPAN_ID = "0" * 16


def is_valid_trace_id(value: str) -> bool:
    """Whether ``value`` is a well-formed, non-zero W3C trace id (32 hex)."""
    return bool(_TRACE_ID_RE.match(value)) and value != _ZERO_TRACE_ID


def is_valid_span_id(value: str) -> bool:
    """Whether ``value`` is a well-formed, non-zero W3C span id (16 hex)."""
    return bool(_SPAN_ID_RE.match(value)) and value != _ZERO_SPAN_ID


def malformed_trace_fields(
    trace_id: str | None,
    span_id: str | None,
    parent_span_id: str | None,
) -> list[str]:
    """Return the names of any present-but-non-conforming trace-context fields."""
    malformed: list[str] = []
    if trace_id is not None and not is_valid_trace_id(trace_id):
        malformed.append("trace_id")
    if span_id is not None and not is_valid_span_id(span_id):
        malformed.append("span_id")
    if parent_span_id is not None and not is_valid_span_id(parent_span_id):
        malformed.append("parent_span_id")
    return malformed
