# Collector

The collector is a small, synchronous daemon that runs on every machine. It
tails usage sources, polls limit sources, and uploads everything to the
server through a durable local queue.

## Design guarantees

- **Crash-safe.** Parse progress (byte offsets per file) and the upload
  queue live in a single SQLite database. Offsets advance only after a
  successful parse; batches leave the queue only after the server confirms
  receipt. A crash at any point loses nothing.
- **Offline-safe.** When the server is unreachable, batches stay queued and
  upload on a later cycle. The daemon never crashes on network errors.
- **Exactly-once (effectively).** The server deduplicates by
  `(provider, event_id)` keeping the max-output row, so re-uploading a batch
  after an ambiguous failure is harmless.
- **Provider-agnostic.** The runner drives the `UsageSource` / `LimitsSource`
  interfaces; sources are built from config by name (`sources.py`). Adding a
  provider needs no runner change.

## Cycle

Each cycle (`collect_once`):

1. **Tail** every usage source: `discover()` files, compare each file's size
   to the stored offset (reset to 0 if the file shrank -- rotation), `parse`
   from the offset, enqueue events in `upload_batch_size` chunks, persist the
   new offset.
2. **Poll** limit sources (on a slower cadence in daemon mode); unavailable
   sources are skipped, not fatal.
3. **Drain** the queue: upload oldest-first until one fails (server down) or
   the queue empties.

## Modes (CLI)

```
tokemetry-collector --config collector.toml            # daemon loop
tokemetry-collector --config collector.toml --once     # one cycle, exit
tokemetry-collector --config collector.toml --dry-run  # parse + report only
tokemetry-collector --config collector.toml --bootstrap  # one-time history import
```

`--dry-run` parses and reports counts without changing state or contacting
the server. `--bootstrap` runs the one-time historical import (guarded by a
state flag so it never re-imports).

## Configuration

TOML file; see `deploy/collector.example.toml`. Key fields: `server_url`,
`api_token`, `machine_name`, `poll_interval_seconds`,
`limits_poll_interval_seconds`, `state_db_path`, and `[sources.*]` /
`[limits.*]` tables with `enabled` flags. Secrets (the API token) live only
in this file on the machine; never commit it.
