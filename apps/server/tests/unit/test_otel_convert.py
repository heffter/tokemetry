"""Exhaustive GenAI span-to-v2 converter coverage (Task 71.4).

One case per row of the semconv mapping table (docs/architecture/otel-mapping.md),
plus content stripping, status/error derivation, latency, and leftover-attribute
retention. The converter is pure, so these are fast unit tests.
"""

from __future__ import annotations

from typing import Any

from tokemetry_core.usage_v2 import EventKind, Finality, SourceType
from tokemetry_server.otel.convert import OtelSpan, span_to_event, spans_to_events
from tokemetry_server.otel.semconv import SEMCONV_VERSION

_TRACE = "0af7651916cd43dd8448eb211c80319c"
_SPAN = "b7ad6b7169203331"
_PARENT = "00f067aa0ba902b7"
_START = 1_783_684_800_000_000_000  # 2026-07-10T12:00:00Z
_END = _START + 2_000_000_000  # +2s


def _span(attributes: dict[str, Any], **over: Any) -> OtelSpan:
    base: dict[str, Any] = {
        "span_id": _SPAN,
        "name": "chat",
        "start_unix_nano": _START,
        "end_unix_nano": _END,
        "attributes": attributes,
        "trace_id": _TRACE,
        "parent_span_id": _PARENT,
    }
    base.update(over)
    return OtelSpan(**base)


def _genai(**extra: Any) -> dict[str, Any]:
    return {
        "gen_ai.system": "OpenAI",
        "gen_ai.request.model": "gpt-5-preview",
        "gen_ai.response.model": "gpt-5",
        **extra,
    }


def test_provider_normalized_and_models_mapped() -> None:
    event = span_to_event(_span(_genai()))
    assert event is not None
    assert event.provider == "openai"  # gen_ai.system lowercased
    assert event.requested_model == "gpt-5-preview"
    assert event.native_model == "gpt-5"  # gen_ai.response.model


def test_native_model_falls_back_to_request_then_unknown() -> None:
    only_request = span_to_event(
        _span({"gen_ai.system": "openai", "gen_ai.request.model": "gpt-5"})
    )
    assert only_request is not None and only_request.native_model == "gpt-5"
    no_models = span_to_event(_span({"gen_ai.system": "openai"}))
    assert no_models is not None and no_models.native_model == "unknown"


def test_token_tiers_mapped() -> None:
    event = span_to_event(
        _span(
            _genai(
                **{
                    "gen_ai.usage.input_tokens": "1000",
                    "gen_ai.usage.output_tokens": "300",
                    "gen_ai.usage.cache_read_tokens": "800",
                    "gen_ai.usage.reasoning_tokens": "120",
                }
            )
        )
    )
    assert event is not None
    assert event.input_tokens == 1000
    assert event.output_tokens == 300
    assert event.cache_read_tokens == 800
    assert event.reasoning_tokens == 120


def test_span_defaults_and_provenance() -> None:
    event = span_to_event(_span(_genai()))
    assert event is not None
    assert event.event_kind is EventKind.ATTEMPT
    assert event.finality is Finality.FINAL
    assert event.sequence == 1
    assert event.source.type is SourceType.SDK
    assert str(event.provenance) == "imported"


def test_trace_context_carried_through() -> None:
    event = span_to_event(_span(_genai()))
    assert event is not None
    assert event.trace_id == _TRACE
    assert event.span_id == _SPAN
    assert event.parent_span_id == _PARENT
    assert event.event_id == _SPAN


def test_latency_derived_and_completed() -> None:
    event = span_to_event(_span(_genai()))
    assert event is not None
    assert event.latency_ms == 2000
    assert event.ts_completed is not None


def test_no_end_time_leaves_latency_and_completed_none() -> None:
    event = span_to_event(_span(_genai(), end_unix_nano=None))
    assert event is not None
    assert event.latency_ms is None
    assert event.ts_completed is None


def test_error_status_maps_to_failure() -> None:
    event = span_to_event(_span(_genai(), status_code="STATUS_CODE_ERROR"))
    assert event is not None
    assert event.success is False
    assert event.outcome == "error"


def test_error_type_attribute_maps_to_failure() -> None:
    event = span_to_event(_span(_genai(**{"error.type": "RateLimit"})))
    assert event is not None
    assert event.success is False


def test_ok_status_is_success() -> None:
    event = span_to_event(_span(_genai(), status_code="STATUS_CODE_OK"))
    assert event is not None
    assert event.success is True
    assert event.outcome == "success"


def test_content_attributes_stripped() -> None:
    event = span_to_event(
        _span(
            _genai(
                **{
                    "gen_ai.prompt": "secret",
                    "gen_ai.completion": "secret",
                    "gen_ai.prompt.0.content": "secret",
                    "gen_ai.input.messages": "secret",
                }
            )
        )
    )
    assert event is not None
    leftovers = event.extra["otel"]["attributes"]
    assert "secret" not in str(event.extra)
    assert all("prompt" not in key and "completion" not in key for key in leftovers)


def test_semconv_version_and_operation_recorded() -> None:
    event = span_to_event(_span(_genai(**{"gen_ai.operation.name": "chat"})))
    assert event is not None
    otel = event.extra["otel"]
    assert otel["semconv_version"] == SEMCONV_VERSION
    assert otel["operation"] == "chat"
    assert otel["system"] == "OpenAI"


def test_unmapped_attributes_retained_in_extra() -> None:
    event = span_to_event(_span(_genai(**{"custom.attr": "value", "server.port": 443})))
    assert event is not None
    leftovers = event.extra["otel"]["attributes"]
    assert leftovers["custom.attr"] == "value"
    assert leftovers["server.port"] == 443


def test_non_genai_span_returns_none() -> None:
    assert span_to_event(_span({"http.method": "GET"})) is None
    assert span_to_event(_span({"gen_ai.system": ""})) is None  # empty is not GenAI


def test_spans_to_events_filters_non_genai() -> None:
    events = spans_to_events(
        [
            _span(_genai(), span_id="a" * 16),
            _span({"http.method": "GET"}, span_id="b" * 16),
            _span(_genai(), span_id="c" * 16),
        ]
    )
    assert len(events) == 2  # the non-GenAI span is dropped
