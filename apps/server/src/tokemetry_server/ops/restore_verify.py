"""Restore verification for backups (Task 70.6, NFR-REL-004, AC-014).

After a backup is restored into a scratch database, :func:`verify_database`
confirms it is trustworthy: the schema is migrated to Alembic head, every
expected table (and view) is present, and the ``daily_rollups`` rows are
internally consistent (each ``total_tokens`` equals the sum of its token
tiers). A tampered or truncated backup fails one of these checks, so a bad
restore is caught before it is relied on.

Run as a CLI against a *synchronous* database URL::

    python -m tokemetry_server.ops.restore_verify sqlite:///restored.db
    python -m tokemetry_server.ops.restore_verify postgresql+psycopg://.../scratch

It prints a report and exits non-zero if verification fails, so the shell
restore-verification job can gate on it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import sqlalchemy as sa
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from tokemetry_server.db.base import Base
from tokemetry_server.db.migrate import alembic_config

#: The token-tier columns whose sum must equal ``daily_rollups.total_tokens``.
_ROLLUP_TIERS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_short_tokens",
    "cache_write_long_tokens",
    "reasoning_tokens",
)


@dataclass
class VerificationReport:
    """The outcome of verifying a restored database."""

    at_head: bool
    current_revision: str | None
    head_revision: str | None
    missing_tables: list[str] = field(default_factory=list)
    rollup_rows: int = 0
    rollup_inconsistencies: int = 0
    table_counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the restore is trustworthy."""
        return (
            self.at_head
            and not self.missing_tables
            and self.rollup_inconsistencies == 0
        )

    def summary(self) -> str:
        """A one-block human-readable report."""
        lines = [
            f"at_head: {self.at_head} "
            f"(current={self.current_revision}, head={self.head_revision})",
            f"tables: {len(self.table_counts)} present, "
            f"missing={self.missing_tables or 'none'}",
            f"daily_rollups: {self.rollup_rows} rows, "
            f"{self.rollup_inconsistencies} inconsistent",
            f"result: {'OK' if self.ok else 'FAILED'}",
        ]
        return "\n".join(lines)


def _expected_relations() -> set[str]:
    """Every table and view the ORM metadata declares."""
    return {table.name for table in Base.metadata.sorted_tables}


def _rollup_inconsistencies(conn: sa.Connection) -> tuple[int, int]:
    """Return (row count, count of rows whose total != tier sum)."""
    columns = ", ".join(_ROLLUP_TIERS)
    rows = conn.execute(
        sa.text(f"SELECT {columns}, total_tokens FROM daily_rollups")
    ).all()
    inconsistent = 0
    for row in rows:
        tier_sum = sum(row[i] for i in range(len(_ROLLUP_TIERS)))
        if tier_sum != row[-1]:
            inconsistent += 1
    return len(rows), inconsistent


def verify_database(sync_url: str) -> VerificationReport:
    """Verify a restored database at ``sync_url`` (a synchronous URL)."""
    config = alembic_config(sync_url)
    head_revision = ScriptDirectory.from_config(config).get_current_head()

    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            current_revision = MigrationContext.configure(conn).get_current_revision()
            inspector = sa.inspect(engine)
            present = set(inspector.get_table_names()) | set(
                inspector.get_view_names()
            )
            expected = _expected_relations()
            missing = sorted(expected - present)

            table_counts: dict[str, int] = {}
            for name in sorted(expected & present):
                count = conn.execute(
                    sa.text(f'SELECT COUNT(*) FROM "{name}"')
                ).scalar_one()
                table_counts[name] = int(count)

            if "daily_rollups" in present:
                rollup_rows, inconsistencies = _rollup_inconsistencies(conn)
            else:
                rollup_rows, inconsistencies = 0, 0
    finally:
        engine.dispose()

    return VerificationReport(
        at_head=current_revision == head_revision,
        current_revision=current_revision,
        head_revision=head_revision,
        missing_tables=missing,
        rollup_rows=rollup_rows,
        rollup_inconsistencies=inconsistencies,
        table_counts=table_counts,
    )


def main(argv: list[str]) -> int:
    """CLI entry: verify the DB at ``argv[0]``; return an exit code."""
    if len(argv) != 1:
        sys.stderr.write("usage: restore_verify <sync_database_url>\n")
        return 2
    report = verify_database(argv[0])
    sys.stdout.write(report.summary() + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
