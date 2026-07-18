"""Shared server test fixtures: a TestClient over a temporary SQLite DB."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings
from tokemetry_server.db.migrate import upgrade_to_head

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
