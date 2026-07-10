"""HTTP tests for the alert rules API."""

from fastapi.testclient import TestClient

_RULE = {
    "name": "opus-limit",
    "kind": "limit_pct",
    "window_kind": "five_hour",
    "threshold": "80",
    "channels": ["ntfy"],
    "cooldown_seconds": 3600,
    "enabled": True,
}


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/alerts").status_code == 401


def test_create_rule_with_dual_thresholds(client: TestClient, auth: dict[str, str]) -> None:
    payload = {**_RULE, "name": "dual", "warn_threshold": "80", "crit_threshold": "95"}
    created = client.post("/api/v1/alerts", json=payload, headers=auth)
    assert created.status_code == 201
    body = created.json()
    assert body["warn_threshold"] == "80"
    assert body["crit_threshold"] == "95"
    assert body["state"] == "normal"
    assert body["last_fired_at"] is None


def test_test_channel_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    # No channel is configured in tests, so delivery fails but the call succeeds.
    response = client.post("/api/v1/alerts/test/ntfy", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"channel": "ntfy", "delivered": False}


def test_rule_crud(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/api/v1/alerts", json=_RULE, headers=auth)
    assert created.status_code == 201
    rule_id = created.json()["id"]
    assert created.json()["channels"] == ["ntfy"]

    listing = client.get("/api/v1/alerts", headers=auth).json()
    assert any(r["name"] == "opus-limit" for r in listing)

    updated = client.put(
        f"/api/v1/alerts/{rule_id}",
        json={**_RULE, "threshold": "90", "enabled": False},
        headers=auth,
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    assert client.delete(f"/api/v1/alerts/{rule_id}", headers=auth).status_code == 204
    assert client.get("/api/v1/alerts", headers=auth).json() == []


def test_invalid_kind_rejected(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/alerts", json={**_RULE, "kind": "bogus"}, headers=auth
    )
    assert response.status_code == 422


def test_duplicate_name_conflicts(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/api/v1/alerts", json=_RULE, headers=auth)
    again = client.post("/api/v1/alerts", json=_RULE, headers=auth)
    assert again.status_code == 409


def test_update_unknown_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.put("/api/v1/alerts/999", json=_RULE, headers=auth).status_code == 404


def test_evaluate_and_events(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/api/v1/alerts", json=_RULE, headers=auth)
    # Seed a breaching limit snapshot so the rule fires.
    client.post(
        "/api/v1/ingest/limits",
        json={
            "machine": {"name": "box-1"},
            "snapshots": [
                {
                    "provider": "anthropic",
                    "ts": "2026-07-09T15:00:00+00:00",
                    "window_kind": "five_hour",
                    "utilization_pct": 92.0,
                }
            ],
        },
        headers=auth,
    )

    evaluated = client.post("/api/v1/alerts/evaluate", headers=auth).json()
    assert len(evaluated["fired"]) == 1
    assert evaluated["fired"][0]["severity"] == "warning"

    events = client.get("/api/v1/alerts/events", headers=auth).json()
    assert any(e["title"].startswith("five_hour at") for e in events)
