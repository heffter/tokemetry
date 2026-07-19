"""Epic TOK-4 acceptance: sources, health, and scoped tokens end to end."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tokemetry_server.db import models
from tokemetry_server.scopes import INGEST_EVENTS


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
        "output_tokens": 100,
        "source": {
            "type": "gateway",
            "name": "aiProviderProxy",
            "version": "1.2.3",
            "instance_id": "proxy-01",
        },
    }


def test_epic_acceptance_end_to_end(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    # 1. Provision an ingest-only, source-scoped token in a single call.
    created = client.post(
        "/api/v1/tokens",
        json={
            "label": "proxy-ingest",
            "scopes": [INGEST_EVENTS],
            "source_allowlist": ["aiProviderProxy"],
        },
        headers=auth,
    )
    assert created.status_code == 201
    proxy_token = created.json()["token"]
    assert created.json()["scopes"] == [INGEST_EVENTS]

    # 2. The proxy ingests a v2 event with its gateway source object.
    ingest = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_event("anthropic:req_1")]},
        headers=_bearer(proxy_token),
    )
    assert ingest.status_code == 200

    # 3. Source type and version are stored; freshness/health is visible via API.
    sources = client.get("/api/v2/sources", headers=auth).json()
    source = next(s for s in sources if s["name"] == "aiProviderProxy")
    assert source["type"] == "gateway"
    assert source["version"] == "1.2.3"
    assert source["health"]["stale"] is False
    assert source["health"]["last_successful_ingest"] is not None
    assert source["health"]["reported_schema_version"] == 2

    # 4. The event is attributed to that source in the ledger.
    with Session(read_engine) as session:
        row = session.get(models.UsageEventV2, ("anthropic", "anthropic:req_1"))
        assert row is not None and row.source_id == source["id"]

    # 5. The ingest-only token is scoped: denied on query endpoints.
    assert client.get("/api/v2/providers", headers=_bearer(proxy_token)).status_code == 403
    assert client.get("/api/v2/sources", headers=_bearer(proxy_token)).status_code == 403

    # 6. Machine tracking stays compatible: v1 ingest still registers a machine
    #    and its derived collector source, distinct from the gateway.
    v1 = client.post(
        "/api/v1/ingest/events",
        json={
            "machine": {"name": "devbox-01", "collector_version": "3.0.0"},
            "events": [
                {
                    "event_id": "req-v1",
                    "provider": "anthropic",
                    "native_model": "claude-sonnet-4-5",
                    "ts": "2026-07-10T12:00:00Z",
                    "output_tokens": 10,
                }
            ],
        },
        headers=auth,
    )
    assert v1.status_code == 200
    with Session(read_engine) as session:
        assert session.get(models.Machine, "devbox-01") is not None
        collector = session.execute(
            sa.select(models.Source).where(models.Source.type == "collector")
        ).scalar_one()
        assert collector.instance_id == "devbox-01"


def test_registered_gateway_fixture(registered_gateway_source: int) -> None:
    """The shared gateway fixture yields a usable source id for later epics."""
    assert registered_gateway_source > 0
