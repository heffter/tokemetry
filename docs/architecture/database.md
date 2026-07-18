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
| `app_settings` | `key` | Runtime key/value settings (UI-editable channel config). |
| `providers` | `id` (lowercase) | Provider registry: display name, aliases, pricing strategy, limit semantics, supported dimensions, `registered` flag. |
| `models` | PK `(provider, native_model_id)` | Model registry: `lifecycle` enum-as-string, `capabilities` JSON, `first_seen`/`last_seen` (last_seen indexed). |
| `model_aliases` | `id`, unique `(provider, alias)` | Maps a provider-specific model spelling to a canonical model id; `rule_version` records the ruleset. |

Money columns use `Numeric(20, 10)` for exact micro-USD arithmetic;
timestamps are `DateTime(timezone=True)`.

### Registry tables (provider-neutral v2)

`providers`, `models`, and `model_aliases` are **lookup data only**: no usage
row carries a foreign key into them (FR-MODEL-007), so registry edits never
rewrite historical events. `providers` is seeded from the core provider
descriptors and augmented by ingest when an unknown provider appears
(`registered=False` marks an observed-but-unknown provider). `models.lifecycle`
is one of `active`, `deprecated`, `retired`, `unknown` (FR-MODEL-004), validated
in the service layer rather than by a database enum so the schema stays
dialect-portable.

**Population** (`services/registries.py`):

- `seed_default_providers` runs at startup and inserts the built-in
  anthropic/openai/zai descriptors on missing ids only (FR-PROVIDER-008) --
  idempotent and non-clobbering, so UI edits survive a restart.
- `ProviderRegistryService.normalize` resolves a raw provider string using the
  core normalizer merged with DB aliases; `resolve` applies the
  `registry_unknown_provider_policy` setting (`accept` marks an unknown
  provider `registered=False` per FR-PROVIDER-005; `reject` refuses the batch).
  A known core seed is always registered even if startup seeding has not run.
- `ModelRegistryService.observe` is called during event ingest for each distinct
  `(provider, native_model)`: a known model's `last_seen` advances, an unknown
  model is inserted with lifecycle `unknown`. The data-quality record for a
  newly observed unknown model is emitted by the recording service (subtask
  61.4); the observation already surfaces the `newly_observed` signal.

Registering a new provider is DB plus seed-data work only, never dashboard code
(FR-PROVIDER-007).

## Migrations

Alembic migrations live in `db/migrations/`; `db/migrate.py` exposes
`upgrade_to_head(sync_url)` / `downgrade_to_base(sync_url)` for server
startup and tests. Alembic runs with a synchronous driver
(`postgresql+psycopg` or `sqlite`) derived from the async application URL by
`Settings.sync_database_url`. Migrations are hand-authored (through `0004`,
which adds the registry tables) and kept in sync with the ORM by a drift test
(`test_migration_matches_orm_metadata`) that reflects the migrated schema
and compares columns against `Base.metadata`.

**Both-engine testing.** From the provider-neutral v2 work onward, the
migration and schema tests are parametrized over both engines via the
`migration_url` / `migrated_engine` fixtures (`tests/conftest.py`). SQLite
always runs; Postgres runs when `TOKEMETRY_TEST_POSTGRES_URL` points at a live
database (a CI service container), resetting the `public` schema around each
test, and skips otherwise. Every schema-changing migration ships upgrade and
downgrade coverage on both engines.

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
