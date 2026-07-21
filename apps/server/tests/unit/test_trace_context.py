"""W3C trace-context validation (Task 71.1)."""

from __future__ import annotations

from tokemetry_server.services.trace_context import (
    is_valid_span_id,
    is_valid_trace_id,
    malformed_trace_fields,
)

_VALID_TRACE = "0af7651916cd43dd8448eb211c80319c"  # 32 hex
_VALID_SPAN = "b7ad6b7169203331"  # 16 hex


def test_valid_ids() -> None:
    assert is_valid_trace_id(_VALID_TRACE)
    assert is_valid_span_id(_VALID_SPAN)


def test_zero_ids_are_invalid() -> None:
    assert not is_valid_trace_id("0" * 32)
    assert not is_valid_span_id("0" * 16)


def test_wrong_length_and_case_invalid() -> None:
    assert not is_valid_trace_id(_VALID_SPAN)  # too short for a trace id
    assert not is_valid_span_id(_VALID_TRACE)  # too long for a span id
    assert not is_valid_trace_id(_VALID_TRACE.upper())  # uppercase not allowed
    assert not is_valid_trace_id("xyz")


def test_malformed_fields_reports_only_present_bad_ids() -> None:
    # All valid -> nothing malformed.
    assert malformed_trace_fields(_VALID_TRACE, _VALID_SPAN, _VALID_SPAN) == []
    # None values are absent, not malformed.
    assert malformed_trace_fields(None, None, None) == []
    # Each bad field is named.
    assert malformed_trace_fields("bad", _VALID_SPAN, None) == ["trace_id"]
    assert malformed_trace_fields(_VALID_TRACE, "bad", "also-bad") == [
        "span_id",
        "parent_span_id",
    ]
