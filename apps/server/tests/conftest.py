"""Shared server test fixtures: a TestClient over a temporary SQLite DB."""

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tokemetry_core.usage_v2 import SourceRef, SourceType
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings
from tokemetry_server.db import models
from tokemetry_server.db.migrate import upgrade_to_head, upgrade_to_revision
from tokemetry_server.services.registries import seed_default_providers
from tokemetry_server.services.sources import SourceRegistryService

#: An aiProviderProxy-shaped gateway source, reused by the proxy and dashboard
#: epics (Tasks 65 and 67) so they exercise a registered gateway consistently.
GATEWAY_SOURCE = SourceRef(
    type=SourceType.GATEWAY,
    name="aiProviderProxy",
    version="1.0.0",
    instance_id="proxy-01",
)

#: v1-only usage-event fields that live under ``extra['_v1']`` in the v2 ledger.
_V1_ONLY_FIELDS = (
    "git_branch",
    "client_version",
    "entrypoint",
    "is_sidechain",
    "session_kind",
    "speed",
    "source",
)


def make_v1_event(**fields: object) -> models.UsageEventV2:
    """Build a ``usage_events_v2`` attempt row from v1-style event fields.

    Since migration 0010 replaced the physical ``usage_events`` table with a
    read-only view over ``usage_events_v2``, tests seed the ledger instead. This
    maps v1 fields the same way v1 ingest does: ``ts`` -> ``ts_started``,
    ``model`` -> ``native_model``, the v1-only columns into ``extra['_v1']``, and
    ``cost_usd`` into the transitional column, so the compatibility view and all
    reads project the row exactly as the old v1 row would have.
    """
    data = dict(fields)
    extra = dict(data.pop("extra", {}) or {})
    v1_only = {name: data.pop(name, None) for name in _V1_ONLY_FIELDS}
    v1_only.setdefault("source", "collector")
    if v1_only.get("is_sidechain") is None:
        v1_only["is_sidechain"] = False
    extra["_v1"] = v1_only
    ts = data.pop("ts")
    return models.UsageEventV2(
        provider=data.pop("provider"),
        event_id=data.pop("event_id"),
        schema_version=2,
        event_kind="attempt",
        finality="final",
        sequence=0,
        native_model=data.pop("model"),
        ts_started=ts,
        ts_completed=ts,
        machine=data.pop("machine", None),
        session_id=data.pop("session_id", None),
        project=data.pop("project", None),
        input_tokens=data.pop("input_tokens", 0),
        output_tokens=data.pop("output_tokens", 0),
        cache_read_tokens=data.pop("cache_read_tokens", 0),
        cache_write_short_tokens=data.pop("cache_write_short_tokens", 0),
        cache_write_long_tokens=data.pop("cache_write_long_tokens", 0),
        reasoning_tokens=0,
        success=True,
        service_tier=data.pop("service_tier", None),
        provenance=str(data.pop("provenance", "local_estimate")),
        cost_usd=data.pop("cost_usd", None),
        dimensions={},
        extra=extra,
        **data,
    )

#: Bootstrap token wired into the test app for authenticated requests.
BOOTSTRAP_TOKEN = "tkm_test_bootstrap_token_value"

#: Env var holding a *synchronous* Postgres URL (e.g. a CI service container)
#: to exercise the schema on Postgres. When unset, Postgres-parametrized tests
#: skip so the both-engine contract is expressed without a local Postgres.
POSTGRES_TEST_URL_ENV = "TOKEMETRY_TEST_POSTGRES_URL"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to the per-test SQLite database file."""
    return tmp_path / "api.db"


@pytest.fixture
def settings(db_path: Path) -> Settings:
    """Settings pointing at the temp DB with a bootstrap token."""
    return Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        api_bootstrap_token=BOOTSTRAP_TOKEN,
        seed_default_alerts=False,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A TestClient whose lifespan runs migrations and wires state."""
    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    """Authorization header carrying the bootstrap token."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


@pytest.fixture
def read_engine(db_path: Path) -> Iterator[sa.Engine]:
    """A synchronous engine for asserting on persisted rows."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def async_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    """An async session over a migrated temp DB for service-level tests."""
    upgrade_to_head(settings.sync_database_url)
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


def _reset_postgres_schema(sync_url: str) -> None:
    """Drop and recreate the ``public`` schema so each test starts empty."""
    engine = sa.create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(sa.text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def migration_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    """A clean, un-migrated synchronous DB URL for each supported engine.

    SQLite always runs against a temp file. Postgres runs only when
    ``TOKEMETRY_TEST_POSTGRES_URL`` is set (a CI service container), resetting
    the schema before and after; otherwise it skips. This expresses the
    both-engine migration contract (baseline doc Section 5.2) without requiring
    a local Postgres.
    """
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'migrate.db'}"
        return
    sync_url = os.environ.get(POSTGRES_TEST_URL_ENV)
    if not sync_url:
        pytest.skip(f"{POSTGRES_TEST_URL_ENV} not set; Postgres engine test skipped")
    _reset_postgres_schema(sync_url)
    try:
        yield sync_url
    finally:
        _reset_postgres_schema(sync_url)


@pytest.fixture
def migrated_engine(migration_url: str) -> Iterator[sa.Engine]:
    """A synchronous engine over a database migrated to head, per engine."""
    upgrade_to_head(migration_url)
    engine = sa.create_engine(migration_url)
    try:
        yield engine
    finally:
        engine.dispose()


#: The last revision before the ``usage_events`` view swap (migration 0010), so
#: tests that need the physical v1 table (the backfill) can still seed it.
PRE_VIEW_REVISION = "0009"


@pytest.fixture
def pre_view_engine(migration_url: str) -> Iterator[sa.Engine]:
    """A synchronous engine migrated to just before the view swap, per engine."""
    upgrade_to_revision(migration_url, PRE_VIEW_REVISION)
    engine = sa.create_engine(migration_url)
    try:
        yield engine
    finally:
        engine.dispose()


#: Representative models for all three initial providers, so every later epic
#: exercises Anthropic, OpenAI, and Z.ai rather than a single vendor
#: (NFR-MAIN-007). Each entry is (provider, native_model_id, lifecycle).
THREE_PROVIDER_MODELS: tuple[tuple[str, str, str], ...] = (
    ("anthropic", "claude-sonnet-4-5", "active"),
    ("anthropic", "claude-opus-4-6", "active"),
    ("anthropic", "claude-haiku-4-5", "active"),
    ("openai", "gpt-5", "active"),
    ("openai", "codex-mini-latest", "active"),
    ("zai", "glm-4.6", "active"),
    ("zai", "glm-4.5-air", "active"),
)

#: Representative model aliases, (provider, alias, native_model_id).
THREE_PROVIDER_MODEL_ALIASES: tuple[tuple[str, str, str], ...] = (
    ("anthropic", "opus", "claude-opus-4-6"),
    ("anthropic", "sonnet", "claude-sonnet-4-5"),
    ("openai", "codex", "codex-mini-latest"),
    ("zai", "glm", "glm-4.6"),
)


async def seed_three_provider_registry(session: AsyncSession) -> None:
    """Seed the three built-in providers plus representative models/aliases.

    Idempotent on providers (delegates to :func:`seed_default_providers`); the
    representative models and aliases are inserted fresh, so call once per test.
    """
    await seed_default_providers(session)
    now = datetime.now(UTC)
    for provider, native_model_id, lifecycle in THREE_PROVIDER_MODELS:
        session.add(
            models.Model(
                provider=provider,
                native_model_id=native_model_id,
                lifecycle=lifecycle,
                capabilities={},
                first_seen=now,
                last_seen=now,
            )
        )
    for provider, alias, native_model_id in THREE_PROVIDER_MODEL_ALIASES:
        session.add(
            models.ModelAlias(
                provider=provider,
                alias=alias,
                native_model_id=native_model_id,
                rule_version=1,
            )
        )


@pytest_asyncio.fixture
async def registry_session(async_session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """An async session with all three providers and representative models seeded."""
    await seed_three_provider_registry(async_session)
    await async_session.commit()
    yield async_session


@pytest_asyncio.fixture
async def registered_gateway_source(async_session: AsyncSession) -> int:
    """A registered aiProviderProxy gateway source; returns its id."""
    source_id = await SourceRegistryService(async_session).resolve_or_create(
        GATEWAY_SOURCE, datetime.now(UTC)
    )
    await async_session.commit()
    return source_id
