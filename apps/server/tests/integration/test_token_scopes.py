"""Migration and API tests for token scopes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_server.db.migrate import upgrade_to_head, upgrade_to_revision
from tokemetry_server.scopes import ALL_SCOPES, INGEST_EVENTS, QUERY_READ


def _scopes_value(raw: object) -> list[str]:
    """Normalize a JSON scopes column read via raw SQL across dialects."""
    if isinstance(raw, str):
        parsed = json.loads(raw)
        assert isinstance(parsed, list)
        return parsed
    assert isinstance(raw, list)
    return raw


def test_existing_tokens_get_full_scopes_on_upgrade(migration_url: str) -> None:
    upgrade_to_revision(migration_url, "0012")
    engine = sa.create_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO api_tokens (label, token_hash, created_at, revoked) "
                    "VALUES ('legacy', 'hash', :created_at, :revoked)"
                ),
                {
                    "created_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
                    "revoked": False,
                },
            )
    finally:
        engine.dispose()

    upgrade_to_head(migration_url)  # runs 0013

    engine = sa.create_engine(migration_url)
    try:
        with engine.connect() as connection:
            raw = connection.execute(
                sa.text("SELECT scopes FROM api_tokens WHERE label = 'legacy'")
            ).scalar()
    finally:
        engine.dispose()
    assert set(_scopes_value(raw)) == set(ALL_SCOPES)


def test_create_token_with_scopes(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/tokens",
        json={"label": "scoped", "scopes": [INGEST_EVENTS, QUERY_READ]},
        headers=auth,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["scopes"] == [INGEST_EVENTS, QUERY_READ]
    assert body["token"]

    listing = client.get("/api/v1/tokens", headers=auth).json()
    scoped = next(row for row in listing if row["label"] == "scoped")
    assert scoped["scopes"] == [INGEST_EVENTS, QUERY_READ]


def test_create_token_default_scopes_are_full(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/v1/tokens", json={"label": "default"}, headers=auth)
    assert response.status_code == 201
    assert set(response.json()["scopes"]) == set(ALL_SCOPES)


def test_create_token_rejects_unknown_scope(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/tokens",
        json={"label": "bad", "scopes": ["ingest:everything"]},
        headers=auth,
    )
    assert response.status_code == 400


def test_create_token_with_source_allowlist(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/tokens",
        json={"label": "allowed", "scopes": [INGEST_EVENTS], "source_allowlist": ["proxy-a"]},
        headers=auth,
    )
    assert response.status_code == 201
    listing = client.get("/api/v1/tokens", headers=auth).json()
    row = next(r for r in listing if r["label"] == "allowed")
    assert row["source_allowlist"] == ["proxy-a"]
