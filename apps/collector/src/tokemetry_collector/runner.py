"""Collector orchestration: tail sources, queue batches, drain the queue.

The collector runs a simple, robust cycle: discover and incrementally parse
each source's files (persisting byte offsets), poll limit sources, enqueue
everything durably, then upload as much of the queue as the server accepts.
Offsets advance only after a successful parse, and batches leave the queue
only after the server confirms receipt, so crashes and outages never lose or
double-count data (the server also deduplicates by event id).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import islice

from loguru import logger
from tokemetry_core.interfaces import LimitsSource, LimitsUnavailableError, UsageSource
from tokemetry_core.models import UsageEvent

from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.state import CollectorState
from tokemetry_collector.uploader import Uploader
from tokemetry_collector.wire import (
    aggregate_to_wire,
    collector_source,
    event_to_wire,
    limit_to_wire_v2,
    machine_info,
)

#: State meta key marking that the one-time bootstrap import has completed.
_BOOTSTRAP_DONE = "bootstrap_done"


@dataclass
class CollectStats:
    """Summary of one collection cycle."""

    files_scanned: int = 0
    events_found: int = 0
    limits_found: int = 0
    batches_enqueued: int = 0
    batches_uploaded: int = 0
    batches_failed: int = 0
    queue_size: int = 0
    unreadable_files: list[str] = field(default_factory=list)


def _chunks(events: list[UsageEvent], size: int) -> Iterable[list[UsageEvent]]:
    """Yield ``events`` in lists of at most ``size``."""
    iterator = iter(events)
    while chunk := list(islice(iterator, size)):
        yield chunk


class Collector:
    """Drives usage/limit sources into the server via a durable queue."""

    def __init__(
        self,
        config: CollectorConfig,
        state: CollectorState,
        uploader: Uploader,
        usage_sources: list[UsageSource],
        limit_sources: list[LimitsSource] | None = None,
        dry_run: bool = False,
    ) -> None:
        """Create the collector.

        Args:
            config: Loaded configuration.
            state: Offset store and upload queue.
            uploader: HTTP uploader.
            usage_sources: Enabled usage sources to tail.
            limit_sources: Enabled limit sources to poll.
            dry_run: When true, parse and report without changing state or
                uploading.
        """
        self._config = config
        self._state = state
        self._uploader = uploader
        self._usage_sources = usage_sources
        self._limit_sources = limit_sources or []
        self._dry_run = dry_run

    def collect_once(self, poll_limits: bool = True) -> CollectStats:
        """Run one full cycle: tail, optionally poll limits, then drain."""
        stats = CollectStats()
        self._tail_usage(stats)
        if poll_limits:
            self._poll_limits(stats)
        if not self._dry_run:
            self._drain(stats)
        stats.queue_size = self._state.queue_size()
        return stats

    def run_bootstrap(self) -> int:
        """Import historical aggregates once; return batches enqueued.

        Guarded by a state flag so re-running the collector does not
        re-import. In dry-run mode nothing is enqueued or recorded.
        """
        if self._state.get_meta(_BOOTSTRAP_DONE) == "1":
            return 0
        machine = machine_info(self._config)
        enqueued = 0
        for source in self._usage_sources:
            aggregates = source.bootstrap()
            if not aggregates or self._dry_run:
                continue
            payload = {
                "machine": machine,
                "aggregates": [aggregate_to_wire(a) for a in aggregates],
            }
            self._state.enqueue("bootstrap", payload)
            enqueued += 1
        if not self._dry_run:
            self._state.set_meta(_BOOTSTRAP_DONE, "1")
        return enqueued

    def run(
        self,
        stop_event: threading.Event,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        """Loop until ``stop_event`` is set, collecting on the poll interval.

        Limit sources are polled on their own (usually slower) cadence.
        """
        clock = monotonic if monotonic is not None else time.monotonic
        last_limits = 0.0
        first = True
        while not stop_event.is_set():
            now = clock()
            do_limits = first or (now - last_limits) >= self._config.limits_poll_interval_seconds
            stats = self.collect_once(poll_limits=do_limits)
            if do_limits:
                last_limits = now
            first = False
            logger.info(
                "cycle: files={} events={} uploaded={} failed={} queued={}",
                stats.files_scanned,
                stats.events_found,
                stats.batches_uploaded,
                stats.batches_failed,
                stats.queue_size,
            )
            stop_event.wait(self._config.poll_interval_seconds)

    def _tail_usage(self, stats: CollectStats) -> None:
        """Discover and incrementally parse every usage source's files."""
        machine = machine_info(self._config)
        for source in self._usage_sources:
            source_key = f"{source.provider}:{type(source).__name__}"
            for file in source.discover():
                stats.files_scanned += 1
                path_str = str(file.path)
                stored = self._state.get_offset(source_key, path_str)
                offset = 0 if stored is None else stored.offset
                if stored is not None and file.size < stored.offset:
                    offset = 0  # file truncated or rotated: re-read from start
                try:
                    result = source.parse(file, offset)
                except OSError as exc:
                    logger.warning("could not read {}: {}", path_str, exc)
                    stats.unreadable_files.append(path_str)
                    continue
                stats.events_found += len(result.events)
                if result.events and not self._dry_run:
                    for chunk in _chunks(list(result.events), self._config.upload_batch_size):
                        payload = {
                            "machine": machine,
                            "events": [event_to_wire(event) for event in chunk],
                        }
                        self._state.enqueue("events", payload)
                        stats.batches_enqueued += 1
                if not self._dry_run:
                    self._state.set_offset(source_key, path_str, result.new_offset, file.size)

    def _poll_limits(self, stats: CollectStats) -> None:
        """Poll each limit source; degrade silently when unavailable."""
        machine_name = self._config.machine_name
        source_ref = collector_source(self._config)
        for source in self._limit_sources:
            try:
                snapshots = source.poll()
            except LimitsUnavailableError as exc:
                logger.info("limits for {} unavailable: {}", source.provider, exc)
                continue
            stats.limits_found += len(snapshots)
            if snapshots and not self._dry_run:
                # v2 limits batch: dimensions ride on each snapshot (Task 76).
                payload = {
                    "schema_version": 2,
                    "snapshots": [
                        limit_to_wire_v2(snapshot, machine_name, source_ref)
                        for snapshot in snapshots
                    ],
                }
                self._state.enqueue("limits", payload)
                stats.batches_enqueued += 1

    def _drain(self, stats: CollectStats) -> None:
        """Upload queued batches until one fails or the queue empties."""
        while True:
            pending = self._state.pending(1)
            if not pending:
                break
            batch = pending[0]
            if self._uploader.send(batch.kind, batch.payload):
                self._state.mark_uploaded(batch.id)
                stats.batches_uploaded += 1
            else:
                self._state.bump_attempts(batch.id)
                stats.batches_failed += 1
                break  # server likely unreachable; retry next cycle
