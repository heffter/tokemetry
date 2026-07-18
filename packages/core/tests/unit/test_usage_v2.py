"""Unit tests for tokemetry_core.usage_v2 (v2 wire model and JSON schema)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tokemetry_core.models import Provenance
from tokemetry_core.usage_v2 import (
    SCHEMA_VERSION,
    EventKind,
    Finality,
    Routing,
    SourceRef,
    SourceType,
    UsageEventV2,
    usage_event_json_schema,
)

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

#: Every top-level property the published wire schema exposes. A change here
#: is a wire-contract change and must be intentional (FR-EVENT-021 forbids
#: any content field ever being added to this set).
_EXPECTED_PROPERTIES = frozenset(
    {
        "schema_version",
        "event_id",
        "event_kind",
        "finality",
        "sequence",
        "logical_request_id",
        "attempt_id",
        "provider_request_id",
        "provider_response_id",
        "provider",
        "native_model",
        "requested_model",
        "routed_model",
        "ts_started",
        "ts_first_token",
        "ts_completed",
        "machine",
        "project",
        "session_id",
        "agent_id",
        "environment",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_short_tokens",
        "cache_write_long_tokens",
        "reasoning_tokens",
        "success",
        "outcome",
        "http_status",
        "stop_reason",
        "service_tier",
        "streaming",
        "latency_ms",
        "time_to_first_token_ms",
        "tool_call_count",
        "tool_histogram",
        "provenance",
        "source",
        "routing",
        "dimensions",
        "extra",
        "trace_id",
        "span_id",
        "parent_span_id",
    }
)


def _source(**overrides: object) -> SourceRef:
    """Build a valid SourceRef, applying keyword overrides."""
    defaults: dict[str, object] = {
        "type": SourceType.GATEWAY,
        "name": "aiProviderProxy",
        "version": "1.2.3",
    }
    defaults.update(overrides)
    return SourceRef.model_validate(defaults)


def _event(**overrides: object) -> UsageEventV2:
    """Build a valid v2 usage event, applying keyword overrides."""
    defaults: dict[str, object] = {
        "schema_version": 2,
        "event_id": "anthropic:req_123",
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "source": _source(),
    }
    defaults.update(overrides)
    return UsageEventV2.model_validate(defaults)


class TestRequiredFields:
    """Identity and lifecycle fields that carry no default."""

    def test_minimal_event_validates(self) -> None:
        event = _event()
        assert event.schema_version == SCHEMA_VERSION
        assert event.event_kind is EventKind.ATTEMPT
        assert event.finality is Finality.FINAL

    def test_schema_version_must_be_two(self) -> None:
        with pytest.raises(ValidationError):
            _event(schema_version=1)

    def test_schema_version_is_required(self) -> None:
        with pytest.raises(ValidationError):
            UsageEventV2.model_validate(
                {
                    "event_id": "x",
                    "event_kind": "attempt",
                    "finality": "final",
                    "sequence": 0,
                    "provider": "anthropic",
                    "native_model": "m",
                    "ts_started": _TS,
                    "source": _source(),
                }
            )

    def test_source_is_required(self) -> None:
        with pytest.raises(ValidationError):
            UsageEventV2.model_validate(
                {
                    "schema_version": 2,
                    "event_id": "x",
                    "event_kind": "attempt",
                    "finality": "final",
                    "sequence": 0,
                    "provider": "anthropic",
                    "native_model": "m",
                    "ts_started": _TS,
                }
            )

    def test_rejects_empty_event_id(self) -> None:
        with pytest.raises(ValidationError):
            _event(event_id="")

    def test_rejects_empty_native_model(self) -> None:
        with pytest.raises(ValidationError):
            _event(native_model="")


class TestEnumsAndLiterals:
    """event_kind, finality, provenance, and source type constraints."""

    def test_rejects_unknown_event_kind(self) -> None:
        with pytest.raises(ValidationError):
            _event(event_kind="streaming")

    def test_rejects_unknown_finality(self) -> None:
        with pytest.raises(ValidationError):
            _event(finality="partial")

    def test_all_event_kinds_accepted(self) -> None:
        for kind in ("attempt", "logical_request", "import", "adjustment"):
            assert _event(event_kind=kind).event_kind == kind

    def test_extended_provenance_values(self) -> None:
        assert _event(provenance="imported").provenance is Provenance.IMPORTED
        assert _event(provenance="adjusted").provenance is Provenance.ADJUSTED

    def test_rejects_unknown_source_type(self) -> None:
        with pytest.raises(ValidationError):
            _event(source=_source(type="proxy"))


class TestTimestamps:
    """Timezone enforcement on the three lifecycle timestamps."""

    def test_rejects_naive_ts_started(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _event(ts_started=datetime(2026, 7, 10, 12, 0, 0))

    def test_rejects_naive_ts_first_token(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _event(ts_first_token=datetime(2026, 7, 10, 12, 0, 1))

    def test_rejects_naive_ts_completed(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _event(ts_completed=datetime(2026, 7, 10, 12, 0, 3))

    def test_optional_timestamps_default_none(self) -> None:
        event = _event()
        assert event.ts_first_token is None
        assert event.ts_completed is None


class TestCounters:
    """Non-negative token counters, including reasoning, and totals."""

    def test_sequence_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            _event(sequence=-1)

    @pytest.mark.parametrize(
        "field",
        [
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_short_tokens",
            "cache_write_long_tokens",
            "reasoning_tokens",
        ],
    )
    def test_rejects_negative_counter(self, field: str) -> None:
        with pytest.raises(ValidationError):
            _event(**{field: -1})

    def test_total_tokens_includes_reasoning(self) -> None:
        event = _event(
            input_tokens=1000,
            output_tokens=300,
            cache_read_tokens=800,
            reasoning_tokens=120,
        )
        assert event.total_tokens == 2220

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _event(latency_ms=-1)


class TestFailedAttempts:
    """Failed and cancelled attempts are ingestible with zero tokens."""

    def test_zero_token_failed_attempt_accepted(self) -> None:
        event = _event(success=False, outcome="error", http_status=500)
        assert event.total_tokens == 0
        assert event.success is False
        assert event.outcome == "error"

    def test_success_defaults_false(self) -> None:
        assert _event().success is False


class TestStrictShape:
    """Immutability, extra=forbid, and nested-model strictness."""

    def test_is_immutable(self) -> None:
        event = _event()
        with pytest.raises(ValidationError):
            event.sequence = 99

    def test_rejects_unknown_top_level_field(self) -> None:
        with pytest.raises(ValidationError):
            _event(prompt="hello")

    def test_routing_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            Routing.model_validate({"policy": "cascade", "temperature": 0.7})

    def test_source_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            SourceRef.model_validate(
                {"type": "gateway", "name": "n", "version": "1", "content": "x"}
            )

    def test_json_round_trip(self) -> None:
        event = _event(
            routing=Routing(policy="cascade", attempt_index=0),
            dimensions={"team": "platform"},
            extra={"anthropic": {"beta": True}},
            trace_id="trace-1",
        )
        restored = UsageEventV2.model_validate_json(event.model_dump_json())
        assert restored == event


class TestJsonSchema:
    """Published JSON schema stability and content-free guarantee."""

    def test_schema_is_deterministic(self) -> None:
        assert usage_event_json_schema() == usage_event_json_schema()

    def test_schema_metadata(self) -> None:
        schema = usage_event_json_schema()
        assert schema["title"] == "UsageEventV2"
        assert schema["x-tokemetry-schema-version"] == SCHEMA_VERSION
        assert schema["$schema"].endswith("2020-12/schema")

    def test_schema_property_set_is_stable(self) -> None:
        schema = usage_event_json_schema()
        assert set(schema["properties"]) == _EXPECTED_PROPERTIES

    def test_schema_required_fields(self) -> None:
        schema = usage_event_json_schema()
        assert set(schema["required"]) == {
            "schema_version",
            "event_id",
            "event_kind",
            "finality",
            "sequence",
            "provider",
            "native_model",
            "ts_started",
            "source",
        }

    def test_schema_has_no_content_fields(self) -> None:
        """FR-EVENT-021: no content-like field may exist in the wire schema."""
        forbidden = ("prompt", "completion", "content", "arguments", "snippet")
        for name in usage_event_json_schema()["properties"]:
            assert not any(token in name.lower() for token in forbidden), name
