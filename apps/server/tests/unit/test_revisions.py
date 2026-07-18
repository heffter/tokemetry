"""Unit tests for the pure revision state machine, projection, and fingerprint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.services.revisions import (
    ConflictMode,
    EventState,
    Outcome,
    RevisionReason,
    resolve,
    row_fingerprint,
    usage_event_v2_row,
)

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _state(
    finality: str = "snapshot",
    sequence: int = 1,
    output_tokens: int = 10,
    fingerprint: str = "fp",
) -> EventState:
    return EventState(finality, sequence, output_tokens, fingerprint)


def _wire_event(**overrides: object) -> UsageEventV2:
    defaults: dict[str, object] = {
        "schema_version": 2,
        "event_id": "anthropic:req_1",
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "source": SourceRef(type=SourceType.GATEWAY, name="proxy", version="1"),
    }
    defaults.update(overrides)
    return UsageEventV2.model_validate(defaults)


class TestResolveNewAndReplay:
    def test_new_id_is_accepted(self) -> None:
        decision = resolve(_state(), None)
        assert decision.outcome is Outcome.ACCEPTED
        assert decision.write is True
        assert decision.archive_reason is None

    def test_identical_replay_is_duplicate(self) -> None:
        decision = resolve(_state(fingerprint="same"), _state(fingerprint="same"))
        assert decision.outcome is Outcome.DUPLICATE
        assert decision.write is False


class TestResolveOverSnapshot:
    def test_higher_snapshot_supersedes(self) -> None:
        decision = resolve(
            _state("snapshot", 2, fingerprint="b"), _state("snapshot", 1, fingerprint="a")
        )
        assert decision.outcome is Outcome.UPDATED
        assert decision.write is True
        assert decision.archive_reason is RevisionReason.SUPERSEDED

    def test_final_supersedes_snapshot(self) -> None:
        decision = resolve(
            _state("final", 1, fingerprint="b"), _state("snapshot", 5, fingerprint="a")
        )
        assert decision.outcome is Outcome.UPDATED
        assert decision.archive_reason is RevisionReason.SUPERSEDED

    def test_same_sequence_differing_is_conflict(self) -> None:
        decision = resolve(
            _state("snapshot", 1, fingerprint="b"), _state("snapshot", 1, fingerprint="a")
        )
        assert decision.outcome is Outcome.REJECTED
        assert decision.write is False
        assert decision.conflict is True

    def test_older_snapshot_is_duplicate(self) -> None:
        decision = resolve(
            _state("snapshot", 1, fingerprint="b"), _state("snapshot", 3, fingerprint="a")
        )
        assert decision.outcome is Outcome.DUPLICATE
        assert decision.write is False


class TestResolveOverFinal:
    def test_snapshot_after_final_is_duplicate(self) -> None:
        decision = resolve(
            _state("snapshot", 9, fingerprint="b"), _state("final", 1, fingerprint="a")
        )
        assert decision.outcome is Outcome.DUPLICATE
        assert decision.write is False

    def test_final_over_final_without_correction_rejected(self) -> None:
        decision = resolve(
            _state("final", 2, fingerprint="b"), _state("final", 1, fingerprint="a")
        )
        assert decision.outcome is Outcome.REJECTED
        assert decision.conflict is True

    def test_final_over_final_with_correction(self) -> None:
        decision = resolve(
            _state("final", 2, fingerprint="b"),
            _state("final", 1, fingerprint="a"),
            correction=True,
        )
        assert decision.outcome is Outcome.CORRECTED
        assert decision.write is True
        assert decision.archive_reason is RevisionReason.CORRECTION


class TestResolveKeepMax:
    def test_identical_is_duplicate(self) -> None:
        decision = resolve(
            _state(output_tokens=5, fingerprint="s"),
            _state(output_tokens=5, fingerprint="s"),
            mode=ConflictMode.KEEP_MAX,
        )
        assert decision.outcome is Outcome.DUPLICATE

    def test_higher_output_updates(self) -> None:
        decision = resolve(
            _state(output_tokens=20, fingerprint="b"),
            _state(output_tokens=10, fingerprint="a"),
            mode=ConflictMode.KEEP_MAX,
        )
        assert decision.outcome is Outcome.UPDATED
        assert decision.write is True
        assert decision.archive_reason is None

    def test_lower_output_is_duplicate(self) -> None:
        decision = resolve(
            _state(output_tokens=5, fingerprint="b"),
            _state(output_tokens=10, fingerprint="a"),
            mode=ConflictMode.KEEP_MAX,
        )
        assert decision.outcome is Outcome.DUPLICATE
        assert decision.write is False

    def test_keep_max_ignores_finality(self) -> None:
        """Keep-max compares only output tokens, never finality/sequence."""
        decision = resolve(
            _state("snapshot", 1, output_tokens=20, fingerprint="b"),
            _state("final", 9, output_tokens=10, fingerprint="a"),
            mode=ConflictMode.KEEP_MAX,
        )
        assert decision.outcome is Outcome.UPDATED


class TestProjection:
    def test_row_matches_table_columns(self) -> None:
        row = usage_event_v2_row(_wire_event())
        assert set(row) == {c.name for c in models.UsageEventV2.__table__.columns}

    def test_timestamps_normalized_to_utc(self) -> None:
        plus_two = timezone(timedelta(hours=2))
        event = _wire_event(ts_started=datetime(2026, 7, 10, 14, 0, 0, tzinfo=plus_two))
        row = usage_event_v2_row(event)
        assert row["ts_started"] == datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

    def test_source_id_defaults_none(self) -> None:
        assert usage_event_v2_row(_wire_event())["source_id"] is None

    def test_enums_projected_as_values(self) -> None:
        row = usage_event_v2_row(_wire_event(event_kind="attempt", finality="final"))
        assert row["event_kind"] == "attempt"
        assert row["finality"] == "final"


class TestFingerprint:
    def test_key_order_independent(self) -> None:
        assert row_fingerprint({"a": 1, "b": 2}) == row_fingerprint({"b": 2, "a": 1})

    def test_naive_and_aware_utc_compare_equal(self) -> None:
        aware = {"ts": datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)}
        naive = {"ts": datetime(2026, 7, 10, 12, 0, 0)}
        assert row_fingerprint(aware) == row_fingerprint(naive)

    def test_differing_content_differs(self) -> None:
        assert row_fingerprint({"output_tokens": 1}) != row_fingerprint({"output_tokens": 2})
