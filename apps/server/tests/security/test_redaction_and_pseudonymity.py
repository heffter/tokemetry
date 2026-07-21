"""Secret redaction and pseudonymized identifiers (Task 70.8).

Secrets never appear in API responses (FR-PRIV-011), and exporter-side hashed
machine/project identifiers flow through the server opaquely (FR-PRIV-004).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from tokemetry_server.scopes import ADMIN_TOKENS

_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}


def test_minted_token_secret_not_echoed_in_audit(
    client: TestClient, auth: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/tokens",
        json={"label": "redact", "scopes": [ADMIN_TOKENS]},
        headers=auth,
    )
    secret = created.json()["token"]
    audit = client.get("/api/v2/admin/audit", headers=auth).json()
    # The one-time secret is nowhere in the audit trail.
    assert secret not in json.dumps(audit)


def test_token_list_never_returns_secrets(
    client: TestClient, auth: dict[str, str]
) -> None:
    client.post(
        "/api/v1/tokens",
        json={"label": "listed", "scopes": [ADMIN_TOKENS]},
        headers=auth,
    )
    listing = client.get("/api/v1/tokens", headers=auth).json()
    for entry in listing:
        assert "token" not in entry  # only metadata, never the secret
        assert "token_hash" not in entry


def _hashed_event(event_id: str, machine: str, project: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "machine": machine,
        "project": project,
        "output_tokens": 10,
        "source": {"type": "gateway", "name": "proxy", "version": "1"},
    }


def test_pseudonymized_identifiers_flow_through_opaquely(
    client: TestClient, auth: dict[str, str]
) -> None:
    """Hashed machine/project names round-trip unchanged (the server is opaque)."""
    machine = "m_" + "a1b2c3d4" * 4
    project = "p_" + "deadbeef" * 4
    client.post(
        "/api/v2/ingest/events",
        json={
            "schema_version": 2,
            "events": [_hashed_event("anthropic:pseudo", machine, project)],
        },
        headers=auth,
    )
    by_machine = client.get(
        "/api/v2/usage",
        params={**_RANGE, "group_by": "machine"},
        headers=auth,
    ).json()["rows"]
    assert any(row["key"] == machine for row in by_machine)
