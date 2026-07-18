"""ORM round-trip, index, and constraint tests for the v2 ledger tables.

Run against every supported engine via ``migrated_engine`` (SQLite always,
Postgres when ``TOKEMETRY_TEST_POSTGRES_URL`` is set) so ``usage_events_v2``,
``usage_event_revisions``, and ``logical_requests`` behave identically on both.
"""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tokemetry_server.db.models import LogicalRequest, UsageEventRevision, UsageEventV2

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

#: The single-column indexes the migration must create on ``usage_events_v2``.
_EXPECTED_V2_INDEXES = {
    "ix_usage_events_v2_logical_request_id",
    "ix_usage_events_v2_provider_request_id",
    "ix_usage_events_v2_native_model",
    "ix_usage_events_v2_ts_started",
    "ix_usage_events_v2_machine",
    "ix_usage_events_v2_session_id",
    "ix_usage_events_v2_outcome",
    "ix_usage_events_v2_source_id",
    "ix_usage_events_v2_trace_id",
    "ix_usage_events_v2_span_id",
    "ix_usage_events_v2_parent_span_id",
}


def _naive_utc(value: datetime) -> datetime:
    """Normalize a possibly tz-aware datetime to a UTC-naive wall clock.

    SQLite returns naive datetimes, Postgres returns tz-aware ones; comparing
    both as UTC-naive keeps the round-trip assertions engine-independent.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _full_event(**overrides: object) -> UsageEventV2:
    """Build a usage_events_v2 row with every column populated."""
    defaults: dict[str, object] = {
        "provider": "anthropic",
        "event_id": "req_1",
        "schema_version": 2,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "logical_request_id": "lr_1",
        "attempt_id": "att_1",
        "provider_request_id": "preq_1",
        "provider_response_id": "presp_1",
        "requested_model": "relayplane:auto",
        "routed_model": "claude-sonnet-4-5",
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "ts_first_token": _TS,
        "ts_completed": _TS,
        "machine": "devbox-01",
        "project": "proj",
        "session_id": "sess_1",
        "agent_id": "agent_1",
        "environment": "development",
        "input_tokens": 1000,
        "output_tokens": 300,
        "cache_read_tokens": 800,
        "cache_write_short_tokens": 10,
        "cache_write_long_tokens": 20,
        "reasoning_tokens": 120,
        "success": True,
        "outcome": "success",
        "http_status": 200,
        "stop_reason": "end_turn",
        "service_tier": "standard",
        "streaming": True,
        "latency_ms": 3000,
        "time_to_first_token_ms": 1000,
        "tool_call_count": 2,
        "tool_histogram": {"read": 3, "write": 1},
        "provenance": "official",
        "source_id": 7,
        "routing": {"policy": "cascade", "reason": "complexity"},
        "dimensions": {"team": "platform"},
        "extra": {"anthropic": {"beta": True}, "gateway": {}},
        "trace_id": "trace_1",
        "span_id": "span_1",
        "parent_span_id": "pspan_1",
    }
    defaults.update(overrides)
    return UsageEventV2(**defaults)


def test_usage_event_v2_full_round_trip(migrated_engine: sa.Engine) -> None:
    """Every column, including JSON payloads, survives a write/read cycle."""
    with Session(migrated_engine) as session:
        session.add(_full_event())
        session.commit()

    with Session(migrated_engine) as session:
        row = session.get(UsageEventV2, ("anthropic", "req_1"))
        assert row is not None
        assert row.schema_version == 2
        assert row.event_kind == "attempt"
        assert row.finality == "final"
        assert row.sequence == 1
        assert row.native_model == "claude-sonnet-4-5"
        assert row.requested_model == "relayplane:auto"
        assert _naive_utc(row.ts_started) == _naive_utc(_TS)
        assert row.reasoning_tokens == 120
        assert row.cache_write_long_tokens == 20
        assert row.success is True
        assert row.outcome == "success"
        assert row.streaming is True
        assert row.latency_ms == 3000
        assert row.tool_call_count == 2
        assert row.tool_histogram == {"read": 3, "write": 1}
        assert row.source_id == 7
        assert row.routing == {"policy": "cascade", "reason": "complexity"}
        assert row.dimensions == {"team": "platform"}
        assert row.extra == {"anthropic": {"beta": True}, "gateway": {}}
        assert row.trace_id == "trace_1"
        assert row.parent_span_id == "pspan_1"


def test_usage_event_v2_optional_columns_nullable(migrated_engine: sa.Engine) -> None:
    """A minimal failed attempt with only required columns persists."""
    with Session(migrated_engine) as session:
        session.add(
            UsageEventV2(
                provider="anthropic",
                event_id="req_min",
                schema_version=2,
                event_kind="attempt",
                finality="final",
                sequence=0,
                native_model="claude-haiku-4-5",
                ts_started=_TS,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_short_tokens=0,
                cache_write_long_tokens=0,
                reasoning_tokens=0,
                success=False,
                tool_call_count=0,
                provenance="local_estimate",
                dimensions={},
                extra={},
            )
        )
        session.commit()

    with Session(migrated_engine) as session:
        row = session.get(UsageEventV2, ("anthropic", "req_min"))
        assert row is not None
        assert row.logical_request_id is None
        assert row.tool_histogram is None
        assert row.routing is None
        assert row.source_id is None
        assert row.output_tokens == 0


def test_usage_event_v2_composite_pk_rejects_duplicate(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(_full_event())
        session.commit()

    with Session(migrated_engine) as session:
        session.add(_full_event(sequence=2))
        with pytest.raises(IntegrityError):
            session.commit()


def test_same_event_id_different_provider_allowed(migrated_engine: sa.Engine) -> None:
    """The provider is part of the grain, so an id may repeat per provider."""
    with Session(migrated_engine) as session:
        session.add(_full_event(provider="anthropic"))
        session.add(_full_event(provider="openai"))
        session.commit()

    with Session(migrated_engine) as session:
        count = session.scalar(sa.select(sa.func.count()).select_from(UsageEventV2))
        assert count == 2


def test_usage_events_v2_indexes_exist(migrated_engine: sa.Engine) -> None:
    index_names = {
        idx["name"] for idx in sa.inspect(migrated_engine).get_indexes("usage_events_v2")
    }
    assert index_names >= _EXPECTED_V2_INDEXES


def test_revision_round_trip_and_index(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            UsageEventRevision(
                provider="anthropic",
                event_id="req_1",
                sequence=1,
                finality="snapshot",
                payload={"output_tokens": 100},
                reason="superseded",
                actor="ingest",
                ts=_TS,
            )
        )
        session.commit()

    with Session(migrated_engine) as session:
        row = session.execute(
            sa.select(UsageEventRevision).where(UsageEventRevision.event_id == "req_1")
        ).scalar_one()
        assert row.reason == "superseded"
        assert row.payload == {"output_tokens": 100}
        assert row.actor == "ingest"

    index_names = {
        idx["name"]
        for idx in sa.inspect(migrated_engine).get_indexes("usage_event_revisions")
    }
    assert "ix_usage_event_revisions_provider_event" in index_names


def test_logical_request_round_trip_and_pk(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            LogicalRequest(
                provider="anthropic",
                logical_request_id="lr_1",
                requested_model="relayplane:auto",
                session_id="sess_1",
                routing_policy="cascade",
                routing_reason="complexity",
                attempt_count=3,
                fallback_count=1,
                winning_attempt_id="att_3",
                ts_first=_TS,
                ts_last=_TS,
            )
        )
        session.commit()

    with Session(migrated_engine) as session:
        row = session.get(LogicalRequest, ("anthropic", "lr_1"))
        assert row is not None
        assert row.attempt_count == 3
        assert row.fallback_count == 1
        assert row.winning_attempt_id == "att_3"

    with Session(migrated_engine) as session:
        session.add(
            LogicalRequest(
                provider="anthropic",
                logical_request_id="lr_1",
                attempt_count=0,
                fallback_count=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
