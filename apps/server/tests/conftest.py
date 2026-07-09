"""Shared server test fixtures: a TestClient over a temporary SQLite DB."""

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
