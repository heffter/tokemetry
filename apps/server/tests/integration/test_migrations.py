"""Migration upgrade/downgrade and ORM/schema drift tests.

These run against every supported engine via the ``migration_url`` fixture:
SQLite always, and Postgres when ``TOKEMETRY_TEST_POSTGRES_URL`` is set (a CI
service container). The schema is dialect-portable, so the same Alembic path
production uses on Postgres is exercised here.
"""

import sqlalchemy as sa
from tokemetry_server.db.base import Base
from tokemetry_server.db.migrate import downgrade_to_base, upgrade_to_head

_EXPECTED_TABLES = {
    "machines",
    "usage_events",
    "limit_snapshots",
    "sessions",
    "daily_rollups",
    "pricing",
    "providers",
    "models",
    "model_aliases",
    "data_quality_events",
    "alert_rules",
    "alert_events",
    "api_tokens",
    "app_settings",
    "alembic_version",
}


def test_upgrade_creates_all_tables(migration_url: str) -> None:
    upgrade_to_head(migration_url)

    engine = sa.create_engine(migration_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert tables >= _EXPECTED_TABLES


def test_downgrade_removes_domain_tables(migration_url: str) -> None:
    upgrade_to_head(migration_url)
    downgrade_to_base(migration_url)

    engine = sa.create_engine(migration_url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()
    domain_tables = _EXPECTED_TABLES - {"alembic_version"}
    assert not (domain_tables & tables)


def test_migration_matches_orm_metadata(migration_url: str) -> None:
    """Every ORM table and column must exist in the migrated schema."""
    upgrade_to_head(migration_url)

    engine = sa.create_engine(migration_url)
    try:
        inspector = sa.inspect(engine)
        for table_name, table in Base.metadata.tables.items():
            db_columns = {col["name"] for col in inspector.get_columns(table_name)}
            orm_columns = {col.name for col in table.columns}
            assert orm_columns == db_columns, f"column drift in {table_name}"
    finally:
        engine.dispose()
