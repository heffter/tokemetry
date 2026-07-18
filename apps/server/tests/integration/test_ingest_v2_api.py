"""Integration tests for the v2 ingest HTTP endpoints."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings

_BOOTSTRAP = "tkm_test_bootstrap_token_value"
_AUTH = {"Authorization": f"Bearer {_BOOTSTRAP}"}


def _event(event_id: str = "anthropic:req_1", **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "output_tokens": 100,
        "source": {"type": "gateway", "name": "proxy", "version": "1.0"},
    }
    event.update(overrides)
    return event


def _batch(events: list[dict[str, Any]], **opts: Any) -> dict[str, Any]:
    return {"schema_version": 2, "events": events, **opts}


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


def test_happy_path(client: TestClient, auth: dict[str, str], read_engine: sa.Engine) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json=_batch([_event("anthropic:a"), _event("anthropic:b")]),
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 2
    assert body["batch_id"]
    assert body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert _count(read_engine, "usage_events_v2") == 2


def test_return_ids_when_requested(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json=_batch([_event("anthropic:c")], return_ids=True),
        headers=auth,
    )
    assert response.json()["accepted_ids"] == ["anthropic:c"]


def test_malformed_event_reports_index(client: TestClient, auth: dict[str, str]) -> None:
    bad = _event("anthropic:bad")
    del bad["native_model"]
    response = client.post(
        "/api/v2/ingest/events",
        json=_batch([_event("anthropic:ok"), bad]),
        headers=auth,
    )
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any(e["index"] == 1 and "native_model" in e["field_path"] for e in errors)


def test_privacy_violation_reports_content_key(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json=_batch([_event("anthropic:p", extra={"anthropic": {"prompt": "x"}})]),
        headers=auth,
    )
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any(e["code"] == "content_key" for e in errors)


def test_gzip_round_trip(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    payload = json.dumps(_batch([_event("anthropic:gz")])).encode("utf-8")
    response = client.post(
        "/api/v2/ingest/events",
        content=gzip.compress(payload),
        headers={**auth, "Content-Encoding": "gzip", "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert _count(read_engine, "usage_events_v2") == 1


def test_validate_does_not_persist(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    response = client.post(
        "/api/v2/ingest/validate",
        json=_batch([_event("anthropic:v")]),
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert _count(read_engine, "usage_events_v2") == 0


def test_validate_reports_errors_without_raising(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.post(
        "/api/v2/ingest/validate",
        json=_batch([_event("anthropic:v2", extra={"anthropic": {"prompt": "x"}})]),
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(e["code"] == "content_key" for e in body["errors"])


def test_readiness_unauthenticated(client: TestClient) -> None:
    response = client.get("/api/v2/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    # Reports the current head revision (a non-empty string), not a fixed value.
    assert isinstance(body["migration"], str) and body["migration"]


def test_oversized_batch_rejected(tmp_path: Path) -> None:
    for test_client in _custom_client(tmp_path, ingest_max_events=2):
        response = test_client.post(
            "/api/v2/ingest/events",
            json=_batch([_event(f"anthropic:e{i}") for i in range(3)]),
            headers=_AUTH,
        )
        assert response.status_code == 413


def test_ingest_and_query_rate_limits_are_separate(tmp_path: Path) -> None:
    for test_client in _custom_client(
        tmp_path, ingest_rate_capacity=2.0, ingest_rate_per_second=0.001
    ):
        # Two validate calls consume the ingest bucket; the third is limited.
        first = test_client.post(
            "/api/v2/ingest/validate", json=_batch([_event()]), headers=_AUTH
        )
        second = test_client.post(
            "/api/v2/ingest/validate", json=_batch([_event()]), headers=_AUTH
        )
        third = test_client.post(
            "/api/v2/ingest/validate", json=_batch([_event()]), headers=_AUTH
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        # Query traffic is a separate class and stays available.
        query = test_client.get("/api/v2/providers", headers=_AUTH)
        assert query.status_code == 200
