"""Integration tests for the v2 registry endpoints (TOK-2, subtask 61.5).

Providers and models listing, provider/lifecycle filters, unknown-model
visibility, alias presence, auth enforcement, and an OpenAPI snapshot asserting
the v2 paths are documented.
"""

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tokemetry_server.db import models

_TS = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)

_MACHINE = {"name": "box-1", "platform": "windows", "collector_version": "0.1.0"}


def _event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": "req_1",
        "provider": "anthropic",
        "native_model": "claude-fable-5",
        "ts": "2026-07-09T09:41:14+00:00",
        "session_id": "sess-1",
        "project": "C:\\devel\\tokemetry",
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_read_tokens": 500,
        "cache_write_short_tokens": 0,
        "cache_write_long_tokens": 200,
    }
    event.update(overrides)
    return event


def _ingest(client: TestClient, auth: dict[str, str], *events: dict[str, Any]) -> None:
    response = client.post(
        "/api/v1/ingest/events",
        json={"machine": _MACHINE, "events": list(events)},
        headers=auth,
    )
    assert response.status_code == 200


class TestProvidersEndpoint:
    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v2/providers").status_code == 401

    def test_lists_seeded_providers(self, client: TestClient, auth: dict[str, str]) -> None:
        rows = client.get("/api/v2/providers", headers=auth).json()
        by_id = {row["id"]: row for row in rows}
        assert {"anthropic", "openai", "zai"} <= set(by_id)
        anthropic = by_id["anthropic"]
        assert anthropic["display_name"] == "Anthropic"
        assert "claude" in anthropic["aliases"]
        assert anthropic["registered"] is True
        assert anthropic["limit_semantics"] == "anthropic_oauth_windows"
        assert "model" in anthropic["supported_dimensions"]

    def test_unknown_provider_visible_as_unregistered(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        _ingest(client, auth, _event(provider="mistral", native_model="mistral-large"))
        rows = client.get("/api/v2/providers", headers=auth).json()
        mistral = next(row for row in rows if row["id"] == "mistral")
        assert mistral["registered"] is False


class TestModelsEndpoint:
    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v2/models").status_code == 401

    def test_observed_model_is_visible_as_unknown(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        _ingest(client, auth, _event())
        rows = client.get("/api/v2/models", headers=auth).json()
        model = next(row for row in rows if row["native_model_id"] == "claude-fable-5")
        assert model["provider"] == "anthropic"
        assert model["lifecycle"] == "unknown"
        assert model["last_seen"].endswith("+00:00")

    def test_filter_by_provider(self, client: TestClient, auth: dict[str, str]) -> None:
        _ingest(
            client,
            auth,
            _event(event_id="a", provider="anthropic", native_model="claude-fable-5"),
            _event(event_id="b", provider="openai", native_model="gpt-x"),
        )
        rows = client.get("/api/v2/models?provider=openai", headers=auth).json()
        assert {row["provider"] for row in rows} == {"openai"}
        assert {row["native_model_id"] for row in rows} == {"gpt-x"}

    def test_filter_by_lifecycle(self, client: TestClient, auth: dict[str, str]) -> None:
        _ingest(client, auth, _event())
        assert client.get("/api/v2/models?lifecycle=active", headers=auth).json() == []
        unknown = client.get("/api/v2/models?lifecycle=unknown", headers=auth).json()
        assert any(row["native_model_id"] == "claude-fable-5" for row in unknown)

    def test_invalid_lifecycle_rejected(self, client: TestClient, auth: dict[str, str]) -> None:
        assert client.get("/api/v2/models?lifecycle=bogus", headers=auth).status_code == 422

    def test_alias_spellings_present(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        with Session(read_engine) as session:
            session.add(
                models.Model(
                    provider="anthropic",
                    native_model_id="claude-opus-4-6",
                    lifecycle="active",
                    capabilities={},
                    first_seen=_TS,
                    last_seen=_TS,
                )
            )
            session.add_all(
                [
                    models.ModelAlias(
                        provider="anthropic",
                        alias="opus",
                        native_model_id="claude-opus-4-6",
                        rule_version=1,
                    ),
                    models.ModelAlias(
                        provider="anthropic",
                        alias="claude-opus",
                        native_model_id="claude-opus-4-6",
                        rule_version=1,
                    ),
                ]
            )
            session.commit()

        rows = client.get("/api/v2/models?provider=anthropic", headers=auth).json()
        model = next(row for row in rows if row["native_model_id"] == "claude-opus-4-6")
        assert model["aliases"] == ["claude-opus", "opus"]


def test_openapi_documents_v2_paths(client: TestClient) -> None:
    """The v2 registry paths are present in the generated OpenAPI schema."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v2/providers" in paths
    assert "/api/v2/models" in paths
    assert "get" in paths["/api/v2/providers"]
    assert "get" in paths["/api/v2/models"]
