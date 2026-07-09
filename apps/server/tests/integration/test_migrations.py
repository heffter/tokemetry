"""Migration upgrade/downgrade and ORM/schema drift tests.

These run against a temporary SQLite database, exercising the same Alembic
migration path production uses on Postgres (the schema is dialect-portable).
"""

from pathlib import Path

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
    "alert_rules",
    "alert_events",
    "api_tokens",
    "alembic_version",
}


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'migrate.db'}"


def test_upgrade_creates_all_tables(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    upgrade_to_head(url)

    engine = sa.create_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert tables >= _EXPECTED_TABLES


def test_downgrade_removes_domain_tables(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    upgrade_to_head(url)
    downgrade_to_base(url)

    engine = sa.create_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()
    domain_tables = _EXPECTED_TABLES - {"alembic_version"}
    assert not (domain_tables & tables)


def test_migration_matches_orm_metadata(tmp_path: Path) -> None:
    """Every ORM table and column must exist in the migrated schema."""
    url = _sqlite_url(tmp_path)
    upgrade_to_head(url)

    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        for table_name, table in Base.metadata.tables.items():
            db_columns = {col["name"] for col in inspector.get_columns(table_name)}
            orm_columns = {col.name for col in table.columns}
            assert orm_columns == db_columns, f"column drift in {table_name}"
    finally:
        engine.dispose()
