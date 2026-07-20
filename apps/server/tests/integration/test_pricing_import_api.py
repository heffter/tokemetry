"""v2 rate-card import API: dry-run diff, digest apply, and scope enforcement (64.9)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.api import pricing as v1_pricing_module
from tokemetry_server.api.v2 import pricing as pricing_module
from tokemetry_server.scopes import ADMIN_PRICING, QUERY_READ

_FIXTURE: dict[str, Any] = {
    "claude-sonnet-4-5": {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 5e-06,
        "output_cost_per_token": 2.5e-05,
    },
    "gpt-5": {
        "litellm_provider": "openai",
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 4e-06,
    },
}


@pytest.fixture(autouse=True)
def _mock_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(_: httpx.AsyncClient) -> dict[str, Any]:
        return _FIXTURE

    monkeypatch.setattr(pricing_module, "fetch_litellm_prices", _fake_fetch)


def _make_token(client: TestClient, auth: dict[str, str], label: str, scopes: list[str]) -> str:
    response = client.post(
        "/api/v1/tokens", json={"label": label, "scopes": scopes}, headers=auth
    )
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def test_dry_run_returns_diff_and_digest(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/v2/pricing/import?dry_run=true", json={}, headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert len(body["digest"]) == 64
    assert body["new"] > 0  # anthropic + openai + curated zai rows
    assert body["changes"], "the structured diff lists the changes"


def test_apply_with_digest_persists_and_is_then_idempotent(
    client: TestClient, auth: dict[str, str]
) -> None:
    dry = client.post("/api/v2/pricing/import?dry_run=true", json={}, headers=auth).json()

    applied = client.post(
        "/api/v2/pricing/import?dry_run=false",
        json={"digest": dry["digest"]},
        headers=auth,
    )
    assert applied.status_code == 200
    assert applied.json()["dry_run"] is False
    assert applied.json()["new"] == dry["new"]

    # Re-running the dry run now shows everything unchanged (rows persisted).
    again = client.post("/api/v2/pricing/import?dry_run=true", json={}, headers=auth).json()
    assert again["new"] == 0 and again["superseded"] == 0


def test_apply_without_digest_is_rejected(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/v2/pricing/import?dry_run=false", json={}, headers=auth)
    assert response.status_code == 400


def test_apply_with_stale_digest_conflicts(client: TestClient, auth: dict[str, str]) -> None:
    # Apply once, then re-apply the original (now stale) digest.
    dry = client.post("/api/v2/pricing/import?dry_run=true", json={}, headers=auth).json()
    client.post(
        "/api/v2/pricing/import?dry_run=false",
        json={"digest": dry["digest"]},
        headers=auth,
    )
    stale = client.post(
        "/api/v2/pricing/import?dry_run=false",
        json={"digest": dry["digest"]},
        headers=auth,
    )
    assert stale.status_code == 409


def test_import_requires_admin_pricing_scope(client: TestClient, auth: dict[str, str]) -> None:
    reader = _make_token(client, auth, "reader", [QUERY_READ])
    forbidden = client.post(
        "/api/v2/pricing/import?dry_run=true",
        json={},
        headers={"Authorization": f"Bearer {reader}"},
    )
    assert forbidden.status_code == 403

    admin = _make_token(client, auth, "pricer", [ADMIN_PRICING])
    allowed = client.post(
        "/api/v2/pricing/import?dry_run=true",
        json={},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert allowed.status_code == 200


def test_v1_sync_litellm_also_feeds_rate_cards(
    client: TestClient, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch,
    read_engine: sa.Engine,
) -> None:
    async def _fake_fetch(_: httpx.AsyncClient) -> dict[str, Any]:
        return _FIXTURE

    monkeypatch.setattr(v1_pricing_module, "fetch_litellm_prices", _fake_fetch)

    response = client.post("/api/v1/pricing/sync-litellm", headers=auth)
    assert response.status_code == 200

    with read_engine.connect() as conn:
        rate_cards = conn.execute(sa.text("SELECT COUNT(*) FROM rate_cards")).scalar()
        audits = conn.execute(
            sa.text("SELECT COUNT(*) FROM audit_log WHERE action = 'pricing_import'")
        ).scalar()
    assert rate_cards and rate_cards > 0  # v2 rate_cards fed by the legacy endpoint
    assert audits == 1  # audited as a v1_sync import
