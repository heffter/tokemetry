"""Integration tests for the ingest endpoints via the HTTP layer."""

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient

_MACHINE = {"name": "box-1", "platform": "windows", "collector_version": "0.1.0"}


def _event(event_id: str = "req_1", output_tokens: int = 100, **overrides: Any) -> dict[str, Any]:
    """Build a valid usage-event payload."""
    event: dict[str, Any] = {
        "event_id": event_id,
        "provider": "anthropic",
        "native_model": "claude-fable-5",
        "ts": "2026-07-09T09:41:14+00:00",
        "session_id": "sess-1",
        "project": "C:\\devel\\tokemetry",
        "input_tokens": 10,
        "output_tokens": output_tokens,
        "cache_read_tokens": 500,
        "cache_write_short_tokens": 0,
        "cache_write_long_tokens": 200,
    }
    event.update(overrides)
    return event


class TestAuth:
    """Authentication behavior across ingest routes."""

    def test_health_is_unauthenticated(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").json() == {"status": "ok"}

    def test_missing_token_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": [_event()]},
        )
        assert response.status_code == 401

    def test_wrong_token_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": [_event()]},
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401


class TestEventIngest:
    """Usage event ingest, dedup, and idempotency."""

    def test_accepts_events(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": [_event("req_1"), _event("req_2")]},
            headers=auth,
        )
        assert response.status_code == 200
        assert response.json() == {"accepted": 2, "duplicates_merged": 0}

        with read_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM usage_events")).scalar_one()
            machines = conn.execute(sa.text("SELECT COUNT(*) FROM machines")).scalar_one()
        assert count == 2
        assert machines == 1

    def test_duplicate_in_batch_merged_keep_max(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={
                "machine": _MACHINE,
                "events": [
                    _event("req_1", output_tokens=1),
                    _event("req_1", output_tokens=648),
                    _event("req_1", output_tokens=100),
                ],
            },
            headers=auth,
        )
        assert response.json() == {"accepted": 1, "duplicates_merged": 2}

        with read_engine.connect() as conn:
            output = conn.execute(
                sa.text("SELECT output_tokens FROM usage_events WHERE event_id='req_1'")
            ).scalar_one()
        assert output == 648

    def test_reingest_is_idempotent_keep_max(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        first = client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": [_event("req_1", output_tokens=648)]},
            headers=auth,
        )
        assert first.status_code == 200
        # A later streaming-snapshot re-send with fewer tokens must not lower it.
        client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": [_event("req_1", output_tokens=5)]},
            headers=auth,
        )

        with read_engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT output_tokens FROM usage_events")).all()
        assert len(rows) == 1
        assert rows[0][0] == 648

    def test_unknown_model_cost_is_null(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        # claude-fable-5 is not in the seeded default pricing table.
        client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": [_event("req_1")]},
            headers=auth,
        )
        with read_engine.connect() as conn:
            cost = conn.execute(sa.text("SELECT cost_usd FROM usage_events")).scalar_one()
        assert cost is None

    def test_known_model_cost_is_computed(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        client.post(
            "/api/v1/ingest/events",
            json={
                "machine": _MACHINE,
                "events": [_event("req_1", native_model="claude-opus-4-5")],
            },
            headers=auth,
        )
        with read_engine.connect() as conn:
            cost = conn.execute(sa.text("SELECT cost_usd FROM usage_events")).scalar_one()
        assert cost is not None
        assert float(cost) > 0

    def test_insane_token_count_rejected(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={
                "machine": _MACHINE,
                "events": [_event("req_1", input_tokens=99_000_000_000)],
            },
            headers=auth,
        )
        assert response.status_code == 400

    def test_negative_token_rejected_by_schema(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": [_event("req_1", output_tokens=-5)]},
            headers=auth,
        )
        assert response.status_code == 422

    def test_empty_batch_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={"machine": _MACHINE, "events": []},
            headers=auth,
        )
        assert response.status_code == 422


class TestLimitIngest:
    """Limit snapshot ingest."""

    def test_accepts_snapshots(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        response = client.post(
            "/api/v1/ingest/limits",
            json={
                "machine": _MACHINE,
                "snapshots": [
                    {
                        "provider": "anthropic",
                        "ts": "2026-07-09T09:00:00+00:00",
                        "window_kind": "five_hour",
                        "utilization_pct": 42.5,
                        "resets_at": "2026-07-09T13:00:00+00:00",
                    }
                ],
            },
            headers=auth,
        )
        assert response.json() == {"accepted": 1, "duplicates_merged": 0}
        with read_engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM limit_snapshots")).scalar_one()
        assert count == 1


class TestBootstrapIngest:
    """Bootstrap aggregate ingest into daily rollups."""

    def test_accepts_and_is_idempotent(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        payload = {
            "machine": _MACHINE,
            "aggregates": [
                {
                    "provider": "anthropic",
                    "day": "2026-06-20",
                    "native_model": "claude-fable-5",
                    "total_tokens": 123456,
                }
            ],
        }
        client.post("/api/v1/ingest/bootstrap", json=payload, headers=auth)
        client.post("/api/v1/ingest/bootstrap", json=payload, headers=auth)

        with read_engine.connect() as conn:
            rows = conn.execute(
                sa.text("SELECT total_tokens, provenance FROM daily_rollups")
            ).all()
        assert len(rows) == 1
        assert rows[0][0] == 123456
        assert rows[0][1] == "stats_cache"
