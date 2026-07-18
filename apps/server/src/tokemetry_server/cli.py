"""Server admin command-line entry points.

Exposes maintenance commands an operator runs out of band:

    python -m tokemetry_server backfill-registries [--force]
    python -m tokemetry_server backfill-usage-events
    python -m tokemetry_server verify-backfill
"""

from __future__ import annotations

import argparse
import asyncio
import json

import sqlalchemy as sa

from tokemetry_server.config import get_settings
from tokemetry_server.db.backfill import backfill_usage_events_v2, verify_backfill
from tokemetry_server.db.migrate import upgrade_to_head
from tokemetry_server.db.session import create_engine, create_session_factory
from tokemetry_server.services.registry_backfill import RegistryBackfill


async def _run_backfill(force: bool) -> None:
    """Run the registry backfill against the configured database."""
    settings = get_settings()
    upgrade_to_head(settings.sync_database_url)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await RegistryBackfill(
                session, settings.data_quality_dedup_window_seconds
            ).run(force=force)
            await session.commit()
    finally:
        await engine.dispose()
    print(
        "registry backfill: "
        f"skipped={result.skipped} providers={result.providers} "
        f"models_active={result.models_active} models_unknown={result.models_unknown}"
    )


def _run_usage_backfill() -> None:
    """Copy v1 usage_events into the v2 ledger (idempotent, resumable)."""
    settings = get_settings()
    upgrade_to_head(settings.sync_database_url)
    engine = sa.create_engine(settings.sync_database_url)
    try:
        with engine.begin() as connection:
            copied = backfill_usage_events_v2(connection)
    finally:
        engine.dispose()
    print(f"usage-event backfill: processed={copied}")


def _run_verify_backfill() -> None:
    """Verify the v1-to-v2 backfill and exit non-zero on any mismatch."""
    settings = get_settings()
    upgrade_to_head(settings.sync_database_url)
    engine = sa.create_engine(settings.sync_database_url)
    try:
        with engine.connect() as connection:
            report = verify_backfill(connection)
    finally:
        engine.dispose()
    print(json.dumps(report.to_dict(), indent=2))
    if not report.ok:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch the requested admin command."""
    parser = argparse.ArgumentParser(
        prog="tokemetry_server", description="tokemetry server admin CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    backfill = subparsers.add_parser(
        "backfill-registries",
        help="Backfill provider/model registries from historical usage data",
    )
    backfill.add_argument(
        "--force",
        action="store_true",
        help="Re-run even if the one-time backfill marker is already set",
    )
    subparsers.add_parser(
        "backfill-usage-events",
        help="Copy v1 usage_events into the v2 ledger (idempotent, resumable)",
    )
    subparsers.add_parser(
        "verify-backfill",
        help="Verify v1-to-v2 backfill count/sum equality; exit 1 on mismatch",
    )
    args = parser.parse_args(argv)

    if args.command == "backfill-registries":
        asyncio.run(_run_backfill(args.force))
    elif args.command == "backfill-usage-events":
        _run_usage_backfill()
    elif args.command == "verify-backfill":
        _run_verify_backfill()


if __name__ == "__main__":
    main()
