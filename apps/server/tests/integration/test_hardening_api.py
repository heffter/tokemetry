"""Transport hardening at the HTTP surface (Task 70.5).

Rate limiting (ingest and query classes, with Retry-After), request-size caps,
CORS, secure headers, and the WebSocket per-token connection cap.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import BOOTSTRAP_TOKEN
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings

_AUTH = {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _build(tmp_path: Path, **overrides: Any) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'harden.db'}",
        api_bootstrap_token=BOOTSTRAP_TOKEN,
        seed_default_alerts=False,
        cost_worker_enabled=False,
        **overrides,
    )
    return TestClient(create_app(settings=settings))


def _event(event_id: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "output_tokens": 10,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }


def test_secure_headers_present(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Strict-Transport-Security" not in response.headers  # HSTS off by default


def test_hsts_when_enabled(tmp_path: Path) -> None:
    with _build(tmp_path, enable_hsts=True) as client:
        response = client.get("/api/v1/health", headers=_AUTH)
        assert "Strict-Transport-Security" in response.headers


def test_oversized_request_rejected(tmp_path: Path) -> None:
    with _build(tmp_path, max_request_bytes=200) as client:
        big = {"schema_version": 2, "events": [_event(f"e{i}") for i in range(50)]}
        response = client.post("/api/v2/ingest/events", json=big, headers=_AUTH)
        assert response.status_code == 413


def test_query_rate_limit_returns_429_with_retry_after(tmp_path: Path) -> None:
    with _build(
        tmp_path, query_rate_capacity=2, query_rate_per_second=0.001
    ) as client:
        assert client.get("/api/v1/tokens", headers=_AUTH).status_code == 200
        assert client.get("/api/v1/tokens", headers=_AUTH).status_code == 200
        limited = client.get("/api/v1/tokens", headers=_AUTH)
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1


def test_ingest_rate_limit_returns_429_with_retry_after(tmp_path: Path) -> None:
    with _build(
        tmp_path, ingest_rate_capacity=1, ingest_rate_per_second=0.001
    ) as client:
        body = {"schema_version": 2, "events": [_event("e1")]}
        assert client.post("/api/v2/ingest/events", json=body, headers=_AUTH).status_code == 200
        limited = client.post(
            "/api/v2/ingest/events",
            json={"schema_version": 2, "events": [_event("e2")]},
            headers=_AUTH,
        )
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1


def test_ingest_limit_does_not_starve_query(tmp_path: Path) -> None:
    """Exhausting the ingest bucket leaves the query class available."""
    with _build(
        tmp_path, ingest_rate_capacity=1, ingest_rate_per_second=0.001
    ) as client:
        body = {"schema_version": 2, "events": [_event("e1")]}
        client.post("/api/v2/ingest/events", json=body, headers=_AUTH)
        client.post(
            "/api/v2/ingest/events",
            json={"schema_version": 2, "events": [_event("e2")]},
            headers=_AUTH,
        )  # ingest now exhausted
        # Query traffic is unaffected.
        assert client.get("/api/v1/tokens", headers=_AUTH).status_code == 200


def test_cors_allows_configured_origin(tmp_path: Path) -> None:
    origin = "https://dash.example"
    with _build(tmp_path, cors_allow_origins=origin) as client:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == origin


def test_cors_denies_unlisted_origin(tmp_path: Path) -> None:
    with _build(tmp_path, cors_allow_origins="https://dash.example") as client:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") is None


@pytest.fixture
def _stream_client(tmp_path: Path) -> Iterator[TestClient]:
    with _build(tmp_path, ws_max_connections_per_token=1) as client:
        yield client


def test_ws_connection_cap(_stream_client: TestClient) -> None:
    """A token already at its connection cap is refused a new stream."""
    # Pre-fill the counter to the limit for the bootstrap token's key.
    app = cast(FastAPI, _stream_client.app)
    app.state.ws_connections[BOOTSTRAP_TOKEN] = 1
    with (
        pytest.raises(WebSocketDisconnect),
        _stream_client.websocket_connect(f"/api/v1/stream?token={BOOTSTRAP_TOKEN}"),
    ):
        pass
