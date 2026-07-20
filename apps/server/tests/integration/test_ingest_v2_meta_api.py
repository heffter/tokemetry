"""Integration tests for the v2 limits and aggregates ingest endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings
from tokemetry_server.db import models

_BOOTSTRAP = "tkm_test_bootstrap_token_value"
_AUTH = {"Authorization": f"Bearer {_BOOTSTRAP}"}


def _snapshot(**overrides: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "schema_version": 2,
        "provider": "anthropic",
        "window_kind": "five_hour",
        "ts": "2026-07-10T12:00:00Z",
        "utilization_pct": 42.5,
    }
    snapshot.update(overrides)
    return snapshot


def _aggregate(**overrides: Any) -> dict[str, Any]:
    aggregate: dict[str, Any] = {
        "schema_version": 2,
        "provider": "anthropic",
        "day": "2026-06-20",
        "native_model": "claude-sonnet-4-5",
        "input_tokens": 100,
        "output_tokens": 50,
    }
    aggregate.update(overrides)
    return aggregate


def _count(engine: sa.Engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def _custom_client(tmp_path: Path, **overrides: Any) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'custom.db'}",
        api_bootstrap_token=_BOOTSTRAP,
        seed_default_alerts=False,
        **overrides,
    )
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client


def test_limits_append_only(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    body = {"schema_version": 2, "snapshots": [_snapshot()]}
    first = client.post("/api/v2/ingest/limits", json=body, headers=auth)
    second = client.post("/api/v2/ingest/limits", json=body, headers=auth)
    assert first.status_code == 200
    assert first.json()["accepted"] == 1
    assert second.status_code == 200
    # Append-only: the identical batch appends a second row (v1 parity).
    assert _count(read_engine, "limit_snapshots") == 2


def test_limits_extended_dimensions_persist_to_columns(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    # Task 69.2: the v2 dimensions now land in dedicated columns, not the raw
    # stash (FR-LIMIT-002/003).
    body = {
        "schema_version": 2,
        "snapshots": [
            _snapshot(
                account="team-a",
                organization="org-x",
                remaining=1000.0,
                limit_amount=5000.0,
                unit="tokens",
                provenance="local_estimate",
            )
        ],
    }
    response = client.post("/api/v2/ingest/limits", json=body, headers=auth)
    assert response.status_code == 200
    with Session(read_engine) as session:
        row = session.execute(sa.select(models.LimitSnapshot)).scalar_one()
        assert row.provenance == "local_estimate"
        assert row.account == "team-a"
        assert row.organization == "org-x"
        assert row.remaining is not None
        assert float(row.remaining) == 1000.0
        assert row.limit_amount is not None
        assert float(row.limit_amount) == 5000.0
        assert row.unit == "tokens"
        assert "v2_dimensions" not in row.raw


def test_limits_structured_error(client: TestClient, auth: dict[str, str]) -> None:
    bad = _snapshot()
    del bad["window_kind"]
    response = client.post(
        "/api/v2/ingest/limits",
        json={"schema_version": 2, "snapshots": [bad]},
        headers=auth,
    )
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any(e["index"] == 0 and "window_kind" in e["field_path"] for e in errors)


def test_aggregates_upsert_idempotent(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    body = {"schema_version": 2, "aggregates": [_aggregate()]}
    first = client.post("/api/v2/ingest/aggregates", json=body, headers=auth)
    second = client.post("/api/v2/ingest/aggregates", json=body, headers=auth)
    assert first.status_code == 200
    assert first.json()["accepted"] == 1
    assert second.status_code == 200
    # Replace-not-accumulate: re-importing the same day converges to one row.
    assert _count(read_engine, "daily_rollups") == 1
    with Session(read_engine) as session:
        row = session.execute(sa.select(models.DailyRollup)).scalar_one()
        assert row.total_tokens == 150
        assert row.provenance == "imported"
        assert row.model == "claude-sonnet-4-5"


def test_aggregates_reasoning_folds_into_total(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    body = {
        "schema_version": 2,
        "aggregates": [_aggregate(reasoning_tokens=200)],
    }
    response = client.post("/api/v2/ingest/aggregates", json=body, headers=auth)
    assert response.status_code == 200
    with Session(read_engine) as session:
        row = session.execute(sa.select(models.DailyRollup)).scalar_one()
        assert row.total_tokens == 350  # 100 + 50 + 200 reasoning


def test_aggregates_dedupe_same_grain_in_batch(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    """Two aggregates on the same grain collapse to the last (replace)."""
    body = {
        "schema_version": 2,
        "aggregates": [
            _aggregate(input_tokens=100, output_tokens=0),
            _aggregate(input_tokens=999, output_tokens=0),
        ],
    }
    response = client.post("/api/v2/ingest/aggregates", json=body, headers=auth)
    assert response.status_code == 200
    assert _count(read_engine, "daily_rollups") == 1
    with Session(read_engine) as session:
        row = session.execute(sa.select(models.DailyRollup)).scalar_one()
        assert row.input_tokens == 999


def test_meta_batch_count_limit(tmp_path: Path) -> None:
    for test_client in _custom_client(tmp_path, ingest_max_events=2):
        response = test_client.post(
            "/api/v2/ingest/limits",
            json={"schema_version": 2, "snapshots": [_snapshot() for _ in range(3)]},
            headers=_AUTH,
        )
        assert response.status_code == 413


def test_ingest_batch_row_recorded(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    response = client.post(
        "/api/v2/ingest/limits",
        json={"schema_version": 2, "snapshots": [_snapshot()]},
        headers=auth,
    )
    batch_id = response.json()["batch_id"]
    with Session(read_engine) as session:
        row = session.get(models.IngestBatch, batch_id)
        assert row is not None
        assert row.accepted == 1
        assert row.schema_version == 2
