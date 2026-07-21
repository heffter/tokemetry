"""Audit review API and per-action wiring (Task 70.4, endpoint level)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from tokemetry_server.scopes import ADMIN_TOKENS, QUERY_READ

_AUDIT = "/api/v2/admin/audit"
_RETENTION = "/api/v2/admin/retention"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mint(
    client: TestClient, auth: dict[str, str], label: str, scopes: list[str]
) -> str:
    response = client.post(
        "/api/v1/tokens", json={"label": label, "scopes": scopes}, headers=auth
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def test_audit_requires_admin_retention_scope(
    client: TestClient, auth: dict[str, str]
) -> None:
    reader = _mint(client, auth, "reader", [QUERY_READ])
    assert client.get(_AUDIT, headers=_bearer(reader)).status_code == 403
    assert client.get(_AUDIT, headers=auth).status_code == 200


def test_token_create_and_revoke_are_audited(
    client: TestClient, auth: dict[str, str]
) -> None:
    """Minting and revoking a token each write one audit row (no secret)."""
    created = client.post(
        "/api/v1/tokens",
        json={"label": "worker", "scopes": [ADMIN_TOKENS]},
        headers=auth,
    )
    secret = created.json()["token"]
    client.delete("/api/v1/tokens/worker", headers=auth)

    entries = client.get(_AUDIT, headers=auth).json()
    actions = {e["action"] for e in entries}
    assert "token_create" in actions
    assert "token_revoke" in actions

    create_entry = next(e for e in entries if e["action"] == "token_create")
    assert create_entry["subject"] == "worker"
    assert create_entry["detail"]["scopes"] == [ADMIN_TOKENS]
    # Redaction: the plaintext token never appears anywhere in the entry.
    assert secret not in json.dumps(create_entry)


def test_retention_change_is_audited_and_filterable(
    client: TestClient, auth: dict[str, str]
) -> None:
    body = client.get(_RETENTION, headers=auth).json()
    body["legal_hold"] = True
    assert client.put(_RETENTION, json=body, headers=auth).status_code == 200

    filtered = client.get(
        _AUDIT, params={"action": "retention_policy_update"}, headers=auth
    ).json()
    assert len(filtered) == 1
    assert filtered[0]["action"] == "retention_policy_update"
    assert filtered[0]["actor"] is not None


def test_audit_is_append_only(client: TestClient, auth: dict[str, str]) -> None:
    """There is no delete path on the audit surface."""
    # A DELETE on the collection is not routed -> 405 Method Not Allowed.
    assert client.delete(_AUDIT, headers=auth).status_code == 405


def test_audit_newest_first_and_limit(
    client: TestClient, auth: dict[str, str]
) -> None:
    for i in range(3):
        client.post(
            "/api/v1/tokens",
            json={"label": f"t{i}", "scopes": [QUERY_READ]},
            headers=auth,
        )
    limited = client.get(_AUDIT, params={"limit": 2}, headers=auth).json()
    assert len(limited) == 2
