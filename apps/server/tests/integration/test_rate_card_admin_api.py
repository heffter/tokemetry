"""v2 rate-card admin API: CRUD, overlap, close, reports, and scope (64.10)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.scopes import ADMIN_PRICING, QUERY_READ


def _card_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "unit_type": "input_token",
        "effective_from": "2026-01-01",
        "unit_price": "0.000005",
    }
    body.update(overrides)
    return body


def _make_token(client: TestClient, auth: dict[str, str], label: str, scopes: list[str]) -> str:
    return client.post(
        "/api/v1/tokens", json={"label": label, "scopes": scopes}, headers=auth
    ).json()["token"]


def test_create_lists_and_reports_version(client: TestClient, auth: dict[str, str]) -> None:
    created = client.post("/api/v2/pricing", json=_card_body(), headers=auth)
    assert created.status_code == 201
    body = created.json()
    assert body["rate_card"]["unit_price"] == "0.000005"
    assert body["pricing_version"]  # current pricing-state version returned

    listed = client.get(
        "/api/v2/pricing?native_model=claude-sonnet-4-5", headers=auth
    ).json()
    assert len(listed) == 1 and listed[0]["unit_type"] == "input_token"


def test_create_overlap_is_rejected(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post("/api/v2/pricing", json=_card_body(), headers=auth).status_code == 201
    dup = client.post("/api/v2/pricing", json=_card_body(unit_price="0.000009"), headers=auth)
    assert dup.status_code == 400


def test_close_sets_effective_to_and_404_for_unknown(
    client: TestClient, auth: dict[str, str]
) -> None:
    card_id = client.post("/api/v2/pricing", json=_card_body(), headers=auth).json()[
        "rate_card"
    ]["id"]
    closed = client.post(
        f"/api/v2/pricing/{card_id}/close",
        json={"effective_to": "2026-06-30"},
        headers=auth,
    )
    assert closed.status_code == 200
    listed = client.get("/api/v2/pricing", headers=auth).json()
    assert listed[0]["effective_to"] == "2026-06-30"

    assert client.post(
        "/api/v2/pricing/999/close", json={"effective_to": "2026-06-30"}, headers=auth
    ).status_code == 404


def test_reports_endpoints_return_lists(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    with read_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO data_quality_events (kind, subject, detail, ts, resolved) "
                "VALUES ('unknown_model', 'openai/o9', "
                "'{\"provider\": \"openai\", \"native_model\": \"o9\"}', "
                "'2026-07-10T12:00:00+00:00', 0)"
            )
        )

    unknown = client.get("/api/v2/pricing/reports/unknown-models", headers=auth)
    assert unknown.status_code == 200
    assert any(row["native_model"] == "o9" for row in unknown.json())

    unpriced = client.get("/api/v2/pricing/reports/unpriced", headers=auth)
    assert unpriced.status_code == 200
    assert isinstance(unpriced.json(), list)


def test_mutations_require_admin_reads_allow_query(
    client: TestClient, auth: dict[str, str]
) -> None:
    reader = {"Authorization": f"Bearer {_make_token(client, auth, 'reader', [QUERY_READ])}"}
    # A reader can list and read reports...
    assert client.get("/api/v2/pricing", headers=reader).status_code == 200
    assert client.get("/api/v2/pricing/reports/unpriced", headers=reader).status_code == 200
    # ...but cannot create.
    assert client.post("/api/v2/pricing", json=_card_body(), headers=reader).status_code == 403

    admin = {"Authorization": f"Bearer {_make_token(client, auth, 'pricer', [ADMIN_PRICING])}"}
    assert client.post("/api/v2/pricing", json=_card_body(), headers=admin).status_code == 201
