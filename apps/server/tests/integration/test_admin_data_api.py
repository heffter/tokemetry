"""Administrative deletion API: dry-run/confirm, scope, legal hold, audit.

Endpoint-level coverage for Task 70.3 (`POST /api/v2/admin/data`).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.scopes import QUERY_READ

_DATA = "/api/v2/admin/data"
_RETENTION = "/api/v2/admin/retention"
_SOURCE = {"type": "gateway", "name": "proxy", "version": "1.0.0"}


def _event(event_id: str, machine: str) -> dict[str, Any]:
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
        "project": "proj",
        "input_tokens": 100,
        "output_tokens": 50,
        "source": dict(_SOURCE),
    }


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> None:
    response = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": events},
        headers=auth,
    )
    assert response.status_code == 200, response.text


def _make_token(
    client: TestClient, auth: dict[str, str], scopes: list[str], label: str
) -> str:
    response = client.post(
        "/api/v1/tokens", json={"label": label, "scopes": scopes}, headers=auth
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _usage_count(client: TestClient, auth: dict[str, str]) -> int:
    response = client.get(
        "/api/v2/usage",
        params={
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-08-01T00:00:00Z",
            "group_by": "provider",
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    return sum(r["attempt_count"] for r in rows)


def _seed(client: TestClient, auth: dict[str, str]) -> None:
    _ingest(client, auth, [_event("e1", "box-a"), _event("e2", "box-a"), _event("e3", "box-b")])


def test_dry_run_reports_counts_without_deleting(
    client: TestClient, auth: dict[str, str]
) -> None:
    _seed(client, auth)
    response = client.post(
        _DATA, params={"dry_run": True}, json={"criteria": {"machine": "box-a"}}, headers=auth
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["counts"]["usage_events_v2"] == 2
    assert body["digest"]
    assert _usage_count(client, auth) == 3  # nothing deleted


def test_confirm_deletes_matching_data(
    client: TestClient, auth: dict[str, str]
) -> None:
    _seed(client, auth)
    dry = client.post(
        _DATA, params={"dry_run": True}, json={"criteria": {"machine": "box-a"}}, headers=auth
    ).json()
    confirm = client.post(
        _DATA,
        params={"dry_run": False},
        json={"criteria": {"machine": "box-a"}, "digest": dry["digest"]},
        headers=auth,
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["executed"] is True
    assert _usage_count(client, auth) == 1  # only box-b remains


def test_confirm_by_source(client: TestClient, auth: dict[str, str]) -> None:
    _seed(client, auth)
    dry = client.post(
        _DATA, params={"dry_run": True}, json={"criteria": {"source": "proxy"}}, headers=auth
    ).json()
    assert dry["counts"]["usage_events_v2"] == 3
    confirm = client.post(
        _DATA,
        params={"dry_run": False},
        json={"criteria": {"source": "proxy"}, "digest": dry["digest"]},
        headers=auth,
    )
    assert confirm.status_code == 200, confirm.text
    assert _usage_count(client, auth) == 0


def test_digest_mismatch_rejected(client: TestClient, auth: dict[str, str]) -> None:
    _seed(client, auth)
    response = client.post(
        _DATA,
        params={"dry_run": False},
        json={"criteria": {"machine": "box-a"}, "digest": "stale"},
        headers=auth,
    )
    assert response.status_code == 409


def test_confirm_requires_digest(client: TestClient, auth: dict[str, str]) -> None:
    _seed(client, auth)
    response = client.post(
        _DATA, params={"dry_run": False}, json={"criteria": {"machine": "box-a"}}, headers=auth
    )
    assert response.status_code == 400


def test_empty_criteria_rejected(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(_DATA, params={"dry_run": True}, json={"criteria": {}}, headers=auth)
    assert response.status_code == 400


def test_requires_admin_retention_scope(
    client: TestClient, auth: dict[str, str]
) -> None:
    read_only = _make_token(client, auth, [QUERY_READ], "reader")
    response = client.post(
        _DATA, params={"dry_run": True}, json={"criteria": {"machine": "box-a"}},
        headers=_bearer(read_only),
    )
    assert response.status_code == 403


def test_legal_hold_blocks_execution(
    client: TestClient, auth: dict[str, str]
) -> None:
    _seed(client, auth)
    dry = client.post(
        _DATA, params={"dry_run": True}, json={"criteria": {"machine": "box-a"}}, headers=auth
    ).json()
    # Turn on the legal hold via the retention policy.
    policy = client.get(_RETENTION, headers=auth).json()
    policy["legal_hold"] = True
    assert client.put(_RETENTION, json=policy, headers=auth).status_code == 200

    confirm = client.post(
        _DATA,
        params={"dry_run": False},
        json={"criteria": {"machine": "box-a"}, "digest": dry["digest"]},
        headers=auth,
    )
    assert confirm.status_code == 409
    assert "legal hold" in confirm.json()["detail"]
    assert _usage_count(client, auth) == 3  # nothing deleted


def test_confirm_writes_audit(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    _seed(client, auth)
    dry = client.post(
        _DATA, params={"dry_run": True}, json={"criteria": {"machine": "box-a"}}, headers=auth
    ).json()
    client.post(
        _DATA,
        params={"dry_run": False},
        json={"criteria": {"machine": "box-a"}, "digest": dry["digest"]},
        headers=auth,
    )
    with read_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT action FROM audit_log WHERE action = 'admin_data_delete'")
        ).all()
    assert len(rows) == 1
