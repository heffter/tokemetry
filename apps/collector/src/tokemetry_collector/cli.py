"""Command-line entry point for the tokemetry collector.

Modes:
- default: run the daemon loop until interrupted.
- ``--once``: run a single collection cycle and exit (useful for cron or a
  scheduled task, and for verifying setup).
- ``--dry-run``: parse and report what would be uploaded without changing
  state or contacting the server.
- ``--bootstrap``: run the one-time historical import (idempotent).
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

from loguru import logger

from tokemetry_collector.config import CollectorConfig, load_config
from tokemetry_collector.runner import Collector
from tokemetry_collector.sources import build_limit_sources, build_usage_sources
from tokemetry_collector.state import CollectorState
from tokemetry_collector.uploader import Uploader


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(prog="tokemetry-collector")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the collector TOML configuration file.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single collection cycle and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without changing state or uploading.",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Run the one-time historical import before collecting.",
    )
    return parser


def _make_collector(
    config: CollectorConfig, dry_run: bool
) -> tuple[Collector, CollectorState, Uploader]:
    """Wire a collector from configuration."""
    state = CollectorState(config.state_db_path)
    uploader = Uploader(config.server_url, config.api_token)
    collector = Collector(
        config=config,
        state=state,
        uploader=uploader,
        usage_sources=build_usage_sources(config),
        limit_sources=build_limit_sources(config),
        dry_run=dry_run,
    )
    return collector, state, uploader


def main(argv: list[str] | None = None) -> int:
    """Entry point for the collector console script."""
    args = _build_parser().parse_args(argv)
    config = load_config(args.config)
    collector, state, uploader = _make_collector(config, args.dry_run)

    try:
        if args.bootstrap:
            enqueued = collector.run_bootstrap()
            logger.info("bootstrap enqueued {} batch(es)", enqueued)

        if args.once or args.dry_run:
            stats = collector.collect_once()
            logger.info(
                "once: files={} events={} uploaded={} failed={} queued={}",
                stats.files_scanned,
                stats.events_found,
                stats.batches_uploaded,
                stats.batches_failed,
                stats.queue_size,
            )
            return 0

        stop_event = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())
        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
        logger.info("collector started for machine {}", config.machine_name)
        collector.run(stop_event)
        return 0
    finally:
        uploader.close()
        state.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
