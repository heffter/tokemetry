"""v2 limits wire serialization (Task 76)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.wire import collector_source, limit_to_wire_v2
from tokemetry_core.models import LimitSnapshot, Provenance

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _config(tmp_path: Path) -> CollectorConfig:
    return CollectorConfig(
        server_url="http://server",
        api_token="tkm_token",
        machine_name="box-1",
        state_db_path=tmp_path / "state.sqlite3",
    )


def test_collector_source_identity(tmp_path: Path) -> None:
    source = collector_source(_config(tmp_path))
    assert source["type"] == "collector"
    assert source["name"] == "box-1"
    assert source["instance_id"] == "box-1"


def test_limit_to_wire_v2_carries_dimensions(tmp_path: Path) -> None:
    snapshot = LimitSnapshot(
        provider="openai",
        ts=_TS,
        machine="box-1",
        window_kind="primary",
        utilization_pct=42.0,
        resets_at=None,
        provenance=Provenance.OFFICIAL,
        account="acct_xyz",
        organization="org-1",
        limit_amount=1000.0,
        remaining=580.0,
        unit="tokens",
    )
    source = collector_source(_config(tmp_path))
    wire = limit_to_wire_v2(snapshot, "box-1", source)

    assert wire["schema_version"] == 2
    assert wire["provider"] == "openai"
    assert wire["window_kind"] == "primary"
    assert wire["utilization_pct"] == 42.0
    assert wire["machine"] == "box-1"
    assert wire["source"] == source
    # The v2 dimensions ride on the snapshot, not in raw.
    assert wire["account"] == "acct_xyz"
    assert wire["organization"] == "org-1"
    assert wire["limit_amount"] == 1000.0
    assert wire["remaining"] == 580.0
    assert wire["unit"] == "tokens"


def test_limit_to_wire_v2_omits_absent_dimensions(tmp_path: Path) -> None:
    snapshot = LimitSnapshot(
        provider="anthropic",
        ts=_TS,
        window_kind="five_hour",
        utilization_pct=10.0,
    )
    wire = limit_to_wire_v2(snapshot, "box-1", collector_source(_config(tmp_path)))
    assert wire["account"] is None
    assert wire["limit_amount"] is None
    assert wire["machine"] == "box-1"  # falls back to the batch machine


def test_wire_validates_against_server_schema(tmp_path: Path) -> None:
    """The collector's v2 output matches the server's LimitSnapshotV2 contract."""
    from tokemetry_core.usage_v2 import LimitSnapshotV2

    snapshot = LimitSnapshot(
        provider="openai",
        ts=_TS,
        window_kind="primary",
        utilization_pct=42.0,
        account="acct_xyz",
        limit_amount=1000.0,
        remaining=580.0,
        unit="tokens",
    )
    wire = limit_to_wire_v2(snapshot, "box-1", collector_source(_config(tmp_path)))
    parsed = LimitSnapshotV2.model_validate(wire)
    assert parsed.account == "acct_xyz"
    assert parsed.remaining == 580.0
    assert parsed.source is not None and parsed.source.name == "box-1"
