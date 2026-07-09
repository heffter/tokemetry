# Database schema (`tokemetry_server.db`)

Postgres in production, SQLite in development and tests. The schema is
deliberately a set of plain, timestamped tables so Grafana can query it
directly; the only dialect-specific element is the JSON column type
(`JSONB` on Postgres, `JSON` elsewhere, via `db/base.py`'s `JSONType`).

## Tables

| Table | Key / grain | Notes |
|---|---|---|
| `machines` | `id` (machine name) | Fleet registry: platform, first/last seen, collector version. |
| `usage_events` | PK `(provider, event_id)` | The idempotency key; ingest keeps the max-output row. Token columns are `BigInteger`. |
| `limit_snapshots` | surrogate `id` | Normalized `(provider, window_kind, utilization_pct, resets_at)` plus `raw` JSON. |
| `sessions` | `session_id` | Per-session rollup: message count, token totals, cost. |
| `daily_rollups` | unique `(day, provider, machine, model, project)` | `''` sentinels for absent dimensions keep upserts deterministic across dialects. |
| `pricing` | unique `(provider, model, effective_date)` | Date-versioned per-MTok rates plus `source`. |
| `alert_rules` | `id`, unique `name` | Condition kind, threshold, channels, cooldown, quiet hours. |
| `alert_events` | `id`, FK `rule_id` | Fired instances with delivery outcome. |
| `api_tokens` | `id`, unique `label`/`token_hash` | Hashed bearer tokens. |

Money columns use `Numeric(20, 10)` for exact micro-USD arithmetic;
timestamps are `DateTime(timezone=True)`.

## Migrations

Alembic migrations live in `db/migrations/`; `db/migrate.py` exposes
`upgrade_to_head(sync_url)` / `downgrade_to_base(sync_url)` for server
startup and tests. Alembic runs with a synchronous driver
(`postgresql+psycopg` or `sqlite`) derived from the async application URL by
`Settings.sync_database_url`. The initial migration (`0001`) is
hand-authored and kept in sync with the ORM by a drift test
(`test_migration_matches_orm_metadata`) that reflects the migrated schema
and compares columns against `Base.metadata`.

## Sessions

`db/session.py` builds the async engine and session factory. `session_scope`
yields a session that commits on success and rolls back on error, used as
the FastAPI dependency and in background tasks.

## Daily rollups

`services/rollups.py` keeps `daily_rollups` current. After each event batch
is upserted, `refresh_rollups_for_days` recomputes the touched days from
`usage_events` (grouped by `(provider, machine, model, project)` with `''`
sentinels for nulls) and upserts them with `provenance='derived'`. Whole
days are recomputed rather than applying deltas, so the rollup always
reflects keep-max updates and re-ingestion is idempotent. Bootstrap imports
write the same table with `provenance='stats_cache'`; a derived refresh for
a day supersedes any bootstrap estimate on the same grain.
