"""Retention admin API: scope enforcement, round-trip, validation, audit.

Endpoint-level coverage for Task 70.1 (`GET`/`PUT /api/v2/admin/retention`).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.scopes import ADMIN_RETENTION, QUERY_READ

_URL = "/api/v2/admin/retention"


def _make_token(
    client: TestClient, auth: dict[str, str], scopes: list[str], label: str
) -> str:
    response = client.post(
        "/api/v1/tokens",
        json={"label": label, "scopes": scopes},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_get_returns_default_policy(client: TestClient, auth: dict[str, str]) -> None:
    """GET returns the PRD defaults when nothing has been overridden."""
    body = client.get(_URL, headers=auth).json()
    assert body["legal_hold"] is False
    assert body["categories"]["raw_events"] == {"retention_days": 180, "enabled": True}
    assert body["categories"]["daily_rollups"] == {
        "retention_days": None,
        "enabled": True,
    }
    assert body["categories"]["v1_archive"] == {"retention_days": None, "enabled": False}


def test_get_requires_admin_retention_scope(
    client: TestClient, auth: dict[str, str]
) -> None:
    """A token without admin:retention is refused; with it, allowed."""
    read_only = _make_token(client, auth, [QUERY_READ], "reader")
    assert client.get(_URL, headers=_bearer(read_only)).status_code == 403

    admin = _make_token(client, auth, [ADMIN_RETENTION], "retention-admin")
    assert client.get(_URL, headers=_bearer(admin)).status_code == 200


def test_put_requires_admin_retention_scope(
    client: TestClient, auth: dict[str, str]
) -> None:
    read_only = _make_token(client, auth, [QUERY_READ], "reader")
    body = client.get(_URL, headers=auth).json()
    assert client.put(_URL, json=body, headers=_bearer(read_only)).status_code == 403


def _current(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    body = client.get(_URL, headers=auth).json()
    assert isinstance(body, dict)
    return body


def test_put_round_trip_persists(client: TestClient, auth: dict[str, str]) -> None:
    """A PUT is reflected by a subsequent GET."""
    body = _current(client, auth)
    body["categories"]["raw_events"]["retention_days"] = 90
    body["categories"]["v1_archive"]["enabled"] = True
    body["legal_hold"] = True

    put = client.put(_URL, json=body, headers=auth)
    assert put.status_code == 200, put.text
    assert put.json()["categories"]["raw_events"]["retention_days"] == 90

    after = _current(client, auth)
    assert after["categories"]["raw_events"]["retention_days"] == 90
    assert after["categories"]["v1_archive"]["enabled"] is True
    assert after["legal_hold"] is True


def test_put_rejects_raw_shorter_than_lag(
    client: TestClient, auth: dict[str, str]
) -> None:
    body = _current(client, auth)
    body["categories"]["raw_events"]["retention_days"] = 1
    response = client.put(_URL, json=body, headers=auth)
    assert response.status_code == 400
    assert "rollup verification lag" in response.json()["detail"]


def test_put_rejects_missing_category(client: TestClient, auth: dict[str, str]) -> None:
    body = _current(client, auth)
    del body["categories"]["audit_records"]
    response = client.put(_URL, json=body, headers=auth)
    assert response.status_code == 400
    assert "audit_records" in response.json()["detail"]


def test_put_writes_audit_entry(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    """A successful PUT records an audit_log row and no earlier rows leak in."""
    body = _current(client, auth)
    body["legal_hold"] = True
    assert client.put(_URL, json=body, headers=auth).status_code == 200

    with read_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT actor, action, subject FROM audit_log "
                "WHERE action = 'retention_policy_update'"
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].subject == "retention"


def test_put_rejects_negative_days_via_schema(
    client: TestClient, auth: dict[str, str]
) -> None:
    """The schema's ge=1 constraint rejects zero/negative durations (422)."""
    body = _current(client, auth)
    body["categories"]["limit_snapshots"]["retention_days"] = 0
    response = client.put(_URL, json=body, headers=auth)
    assert response.status_code == 422


def test_status_returns_every_category(
    client: TestClient, auth: dict[str, str]
) -> None:
    """GET /status lists every category with its policy and (empty) counters."""
    body = client.get(f"{_URL}/status", headers=auth).json()
    assert body["legal_hold"] is False
    names = {c["category"] for c in body["categories"]}
    assert "raw_events" in names and "v1_archive" in names
    raw = next(c for c in body["categories"] if c["category"] == "raw_events")
    assert raw["retention_days"] == 180
    assert raw["last_deleted"] == 0
    assert raw["last_run_at"] is None


def test_status_requires_admin_retention_scope(
    client: TestClient, auth: dict[str, str]
) -> None:
    read_only = _make_token(client, auth, [QUERY_READ], "reader")
    assert (
        client.get(f"{_URL}/status", headers=_bearer(read_only)).status_code == 403
    )
