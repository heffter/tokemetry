"""Unit tests for the v2 limit-snapshot and aggregate-import wire models."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError
from tokemetry_core.models import Provenance
from tokemetry_core.usage_v2 import AggregateImportV2, LimitSnapshotV2

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _snapshot(**overrides: object) -> LimitSnapshotV2:
    defaults: dict[str, object] = {
        "schema_version": 2,
        "provider": "anthropic",
        "window_kind": "five_hour",
        "ts": _TS,
        "utilization_pct": 42.5,
    }
    defaults.update(overrides)
    return LimitSnapshotV2.model_validate(defaults)


class TestLimitSnapshotV2:
    def test_valid_minimal(self) -> None:
        snapshot = _snapshot()
        assert snapshot.provenance is Provenance.OFFICIAL
        assert snapshot.account is None
        assert snapshot.remaining is None

    def test_extended_dimensions(self) -> None:
        snapshot = _snapshot(
            account="team-a",
            organization="org-1",
            remaining=1000.0,
            limit_amount=5000.0,
            unit="tokens",
        )
        assert snapshot.account == "team-a"
        assert snapshot.limit_amount == 5000.0

    def test_rejects_naive_ts(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _snapshot(ts=datetime(2026, 7, 10, 12, 0, 0))

    def test_rejects_naive_resets_at(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _snapshot(resets_at=datetime(2026, 7, 10, 17, 0, 0))

    def test_rejects_negative_remaining(self) -> None:
        with pytest.raises(ValidationError):
            _snapshot(remaining=-1.0)

    def test_estimated_provenance(self) -> None:
        assert _snapshot(provenance="local_estimate").provenance is Provenance.LOCAL_ESTIMATE


class TestAggregateImportV2:
    def test_total_derived_including_reasoning(self) -> None:
        aggregate = AggregateImportV2(
            schema_version=2,
            provider="anthropic",
            day=date(2026, 6, 20),
            native_model="claude-sonnet-4-5",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=1000,
            reasoning_tokens=120,
        )
        assert aggregate.total_tokens == 1270

    def test_explicit_total_kept(self) -> None:
        aggregate = AggregateImportV2(
            schema_version=2,
            provider="anthropic",
            day=date(2026, 6, 20),
            native_model="m",
            total_tokens=999,
        )
        assert aggregate.total_tokens == 999

    def test_provenance_defaults_imported(self) -> None:
        aggregate = AggregateImportV2(
            schema_version=2, provider="anthropic", day=date(2026, 6, 20), native_model="m"
        )
        assert aggregate.provenance is Provenance.IMPORTED
