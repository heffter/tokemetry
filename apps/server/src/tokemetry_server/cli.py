"""Server admin command-line entry points.

Currently exposes the registry backfill so an operator can reconcile the
provider/model registries from historical usage after a recovery:

    python -m tokemetry_server backfill-registries [--force]
"""

from __future__ import annotations

import argparse
import asyncio

from tokemetry_server.config import get_settings
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
    args = parser.parse_args(argv)

    if args.command == "backfill-registries":
        asyncio.run(_run_backfill(args.force))


if __name__ == "__main__":
    main()
