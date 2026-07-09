"""Unit tests for tokemetry_core.models."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tokemetry_core.models import (
    DailyAggregate,
    LimitSnapshot,
    ParseResult,
    PriceRow,
    Provenance,
    UsageEvent,
)

_TS = datetime(2026, 7, 9, 10, 0, 0, tzinfo=UTC)


def _event(**overrides: object) -> UsageEvent:
    """Build a valid UsageEvent, applying keyword overrides."""
    defaults: dict[str, object] = {
        "event_id": "req_1",
        "provider": "fake",
        "native_model": "fake-model-1",
        "ts": _TS,
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 30,
        "cache_write_short_tokens": 40,
        "cache_write_long_tokens": 50,
    }
    defaults.update(overrides)
    return UsageEvent.model_validate(defaults)


class TestUsageEvent:
    """Validation and behavior of the normalized usage event."""

    def test_total_tokens_sums_all_categories(self) -> None:
        assert _event().total_tokens == 150

    def test_rejects_naive_timestamp(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _event(ts=datetime(2026, 7, 9, 10, 0, 0))

    def test_rejects_negative_tokens(self) -> None:
        with pytest.raises(ValidationError):
            _event(output_tokens=-1)

    def test_rejects_empty_event_id(self) -> None:
        with pytest.raises(ValidationError):
            _event(event_id="")

    def test_is_immutable(self) -> None:
        event = _event()
        with pytest.raises(ValidationError):
            event.input_tokens = 999

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            _event(unknown_field=1)

    def test_json_round_trip(self) -> None:
        event = _event(extra={"web_search_requests": 2})
        restored = UsageEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_defaults_are_local_estimate_and_zero(self) -> None:
        event = UsageEvent(
            event_id="req_2", provider="fake", native_model="m", ts=_TS
        )
        assert event.provenance is Provenance.LOCAL_ESTIMATE
        assert event.total_tokens == 0
        assert event.is_sidechain is False


class TestDailyAggregate:
    """Bootstrap aggregate token totals."""

    def test_total_derived_from_split_fields(self) -> None:
        aggregate = DailyAggregate(
            provider="anthropic",
            day=date(2026, 6, 20),
            native_model="claude-fable-5",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=1000,
        )
        assert aggregate.total_tokens == 1150

    def test_explicit_total_kept_when_split_absent(self) -> None:
        aggregate = DailyAggregate(
            provider="anthropic",
            day=date(2026, 6, 20),
            native_model="claude-fable-5",
            total_tokens=123456,
        )
        assert aggregate.total_tokens == 123456

    def test_provenance_defaults_to_stats_cache(self) -> None:
        aggregate = DailyAggregate(
            provider="anthropic",
            day=date(2026, 6, 20),
            native_model="m",
        )
        assert aggregate.provenance is Provenance.STATS_CACHE


class TestLimitSnapshot:
    """Validation of limit window snapshots."""

    def test_valid_snapshot(self) -> None:
        snapshot = LimitSnapshot(
            provider="fake",
            ts=_TS,
            window_kind="five_hour",
            utilization_pct=87.5,
            resets_at=_TS,
        )
        assert snapshot.utilization_pct == 87.5

    def test_rejects_naive_resets_at(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            LimitSnapshot(
                provider="fake",
                ts=_TS,
                window_kind="five_hour",
                utilization_pct=10.0,
                resets_at=datetime(2026, 7, 9, 15, 0, 0),
            )

    def test_rejects_negative_utilization(self) -> None:
        with pytest.raises(ValidationError):
            LimitSnapshot(
                provider="fake", ts=_TS, window_kind="w", utilization_pct=-0.1
            )


class TestParseResult:
    """Parse result semantics."""

    def test_defaults_to_empty(self) -> None:
        result = ParseResult()
        assert result.events == ()
        assert result.new_offset == 0

    def test_rejects_negative_offset(self) -> None:
        with pytest.raises(ValidationError):
            ParseResult(new_offset=-1)


class TestPriceRow:
    """Price row validation."""

    def test_valid_row(self) -> None:
        row = PriceRow(
            provider="anthropic",
            model="claude-opus-4-5",
            effective_date=date(2026, 1, 1),
            input_per_mtok=Decimal("5"),
            output_per_mtok=Decimal("25"),
            cache_read_per_mtok=Decimal("0.5"),
            cache_write_short_per_mtok=Decimal("6.25"),
            cache_write_long_per_mtok=Decimal("10"),
        )
        assert row.input_per_mtok == Decimal("5")

    def test_rejects_negative_price(self) -> None:
        with pytest.raises(ValidationError):
            PriceRow(
                provider="anthropic",
                model="m",
                effective_date=date(2026, 1, 1),
                input_per_mtok=Decimal("-1"),
                output_per_mtok=Decimal("0"),
                cache_read_per_mtok=Decimal("0"),
                cache_write_short_per_mtok=Decimal("0"),
                cache_write_long_per_mtok=Decimal("0"),
            )
