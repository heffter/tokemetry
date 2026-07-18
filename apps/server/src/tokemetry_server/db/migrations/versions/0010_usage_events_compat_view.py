"""replace usage_events with the v1 compatibility view

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from tokemetry_server.db.backfill import verify_backfill

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: SQLite projection: JSON1 ``json_extract`` for the v1-only fields, and
#: ``json_remove`` to strip the internal ``_v1``/``_backfill`` keys from ``extra``.
_SQLITE_VIEW = """
CREATE VIEW usage_events AS
SELECT
    provider,
    event_id,
    machine,
    session_id,
    ts_started AS ts,
    native_model AS model,
    project,
    json_extract(extra, '$._v1.git_branch') AS git_branch,
    json_extract(extra, '$._v1.client_version') AS client_version,
    json_extract(extra, '$._v1.entrypoint') AS entrypoint,
    json_extract(extra, '$._v1.is_sidechain') AS is_sidechain,
    json_extract(extra, '$._v1.session_kind') AS session_kind,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_write_short_tokens,
    cache_write_long_tokens,
    service_tier,
    json_extract(extra, '$._v1.speed') AS speed,
    cost_usd,
    provenance,
    json_extract(extra, '$._v1.source') AS source,
    json_remove(extra, '$._v1', '$._backfill') AS extra
FROM usage_events_v2
WHERE event_kind = 'attempt'
"""

#: Postgres projection: ``#>>`` path extraction with a boolean cast for
#: ``is_sidechain`` and ``-`` key removal to clean ``extra``.
_POSTGRES_VIEW = """
CREATE VIEW usage_events AS
SELECT
    provider,
    event_id,
    machine,
    session_id,
    ts_started AS ts,
    native_model AS model,
    project,
    extra #>> '{_v1,git_branch}' AS git_branch,
    extra #>> '{_v1,client_version}' AS client_version,
    extra #>> '{_v1,entrypoint}' AS entrypoint,
    (extra #>> '{_v1,is_sidechain}')::boolean AS is_sidechain,
    extra #>> '{_v1,session_kind}' AS session_kind,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_write_short_tokens,
    cache_write_long_tokens,
    service_tier,
    extra #>> '{_v1,speed}' AS speed,
    cost_usd,
    provenance,
    extra #>> '{_v1,source}' AS source,
    (extra - '_v1' - '_backfill') AS extra
FROM usage_events_v2
WHERE event_kind = 'attempt'
"""


def upgrade() -> None:
    """Verify the backfill, then swap usage_events for the v1 view (D-001).

    Aborts the migration if the count/sum verification finds any mismatch, so
    the physical table is never replaced over an inconsistent ledger.
    """
    connection = op.get_bind()
    report = verify_backfill(connection)
    if not report.ok:
        raise RuntimeError(
            "v1-to-v2 backfill verification failed; refusing to swap "
            f"usage_events to a view. Mismatches: {report.mismatches}"
        )

    op.rename_table("usage_events", "usage_events_v1_archive")
    view_sql = _SQLITE_VIEW if connection.dialect.name == "sqlite" else _POSTGRES_VIEW
    op.execute(view_sql)


def downgrade() -> None:
    """Drop the view and restore the physical table from the archive."""
    op.execute("DROP VIEW usage_events")
    op.rename_table("usage_events_v1_archive", "usage_events")
