"""End-to-end acceptance checks for the provider/model registry epic (TOK-2).

Exercises the epic acceptance criteria across all three initial providers
(NFR-MAIN-007): central alias normalization, native provider-scoped model ids,
versioned aliases kept separate from native ids, unknown entities producing
data-quality records, and existing Claude data staying queryable through v1.
The v1 golden suite (``test_v1_golden``) covers wire compatibility and runs in
the same suite.
"""

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.normalization import normalize_provider
from tokemetry_server.db import models
from tokemetry_server.services.registries import ProviderRegistryService

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


class TestAliasNormalization:
    """FR-PROVIDER-002/003: aliases normalize centrally to lowercase ids."""

    async def test_zai_aliases_land_as_zai(self, registry_session: AsyncSession) -> None:
        service = ProviderRegistryService(registry_session)
        assert await service.normalize("z.ai") == "zai"
        assert await service.normalize("Z-AI") == "zai"
        assert await service.normalize("  Z.AI  ") == "zai"
        # The core normalizer is the single source, independent of the DB.
        assert normalize_provider("z.ai") == "zai"

    async def test_all_family_aliases_resolve(self, registry_session: AsyncSession) -> None:
        service = ProviderRegistryService(registry_session)
        assert await service.normalize("claude-code") == "anthropic"
        assert await service.normalize("codex-cli") == "openai"


class TestProviderRegistration:
    """FR-PROVIDER-008: Anthropic, OpenAI, and Z.ai are registered."""

    async def test_three_providers_registered(self, registry_session: AsyncSession) -> None:
        rows = {
            row.id: row
            for row in (
                await registry_session.execute(select(models.Provider))
            ).scalars()
        }
        for provider_id in ("anthropic", "openai", "zai"):
            assert provider_id in rows
            assert rows[provider_id].registered is True


class TestModelIdentity:
    """FR-MODEL-001/002/009: native, provider-scoped ids; versioned aliases."""

    async def test_model_ids_are_native_and_provider_scoped(
        self, registry_session: AsyncSession
    ) -> None:
        # Representative native ids are retained verbatim under each provider.
        opus = await registry_session.get(models.Model, ("anthropic", "claude-opus-4-6"))
        glm = await registry_session.get(models.Model, ("zai", "glm-4.6"))
        assert opus is not None and glm is not None

        # The same native id under a different provider is a distinct row.
        registry_session.add(
            models.Model(
                provider="openai",
                native_model_id="glm-4.6",
                lifecycle="active",
                capabilities={},
            )
        )
        await registry_session.commit()
        count = await registry_session.scalar(
            select(func.count())
            .select_from(models.Model)
            .where(models.Model.native_model_id == "glm-4.6")
        )
        assert count == 2

    async def test_aliases_are_versioned_and_separate(
        self, registry_session: AsyncSession
    ) -> None:
        alias = (
            await registry_session.execute(
                select(models.ModelAlias).where(models.ModelAlias.alias == "opus")
            )
        ).scalar_one()
        assert alias.native_model_id == "claude-opus-4-6"
        assert alias.rule_version == 1
        # The alias table is separate from the native model row.
        assert await registry_session.get(models.Model, ("anthropic", "opus")) is None


class TestUnknownEntities:
    """FR-PROVIDER-005 / FR-MODEL-006: unknowns accepted and flagged."""

    def test_unknown_provider_and_model_recorded(
        self, client: TestClient, auth: dict[str, str], read_engine: sa.Engine
    ) -> None:
        response = client.post(
            "/api/v1/ingest/events",
            json={
                "machine": _MACHINE,
                "events": [_event(provider="mistral", native_model="mistral-large-3")],
            },
            headers=auth,
        )
        assert response.status_code == 200

        with read_engine.connect() as conn:
            provider_registered = conn.execute(
                sa.text("SELECT registered FROM providers WHERE id = 'mistral'")
            ).scalar_one()
            dq_kinds = {
                row[0]
                for row in conn.execute(
                    sa.text("SELECT DISTINCT kind FROM data_quality_events")
                )
            }
        assert bool(provider_registered) is False
        assert "unknown_provider" in dq_kinds
        assert "unknown_model" in dq_kinds


class TestV1Compatibility:
    """FR-PROVIDER-009: existing Claude data stays queryable through v1."""

    def test_claude_usage_queryable_via_v1(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        assert (
            client.post(
                "/api/v1/ingest/events",
                json={"machine": _MACHINE, "events": [_event()]},
                headers=auth,
            ).status_code
            == 200
        )
        response = client.get(
            "/api/v1/usage?group_by=model&from=2026-07-09&to=2026-07-09",
            headers=auth,
        )
        assert response.status_code == 200
        keys = {bucket["key"] for bucket in response.json()["buckets"]}
        assert "claude-fable-5" in keys

    def test_v2_providers_lists_all_three(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        # FR-PROVIDER-010: registry metadata is queryable through the API.
        ids = {row["id"] for row in client.get("/api/v2/providers", headers=auth).json()}
        assert {"anthropic", "openai", "zai"} <= ids
