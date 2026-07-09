"""Unit tests for server configuration."""

from tokemetry_server.config import Settings


def test_defaults_to_local_sqlite() -> None:
    settings = Settings()
    assert settings.database_url.startswith("sqlite+aiosqlite:")


def test_sync_url_translates_asyncpg() -> None:
    settings = Settings(database_url="postgresql+asyncpg://u:p@host/db")
    assert settings.sync_database_url == "postgresql+psycopg://u:p@host/db"


def test_sync_url_translates_aiosqlite() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///./x.db")
    assert settings.sync_database_url == "sqlite:///./x.db"


def test_sync_url_passthrough_for_plain_url() -> None:
    settings = Settings(database_url="sqlite:///./x.db")
    assert settings.sync_database_url == "sqlite:///./x.db"


def test_env_prefix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TOKEMETRY_BIND_PORT", "9999")
    settings = Settings()
    assert settings.bind_port == 9999
