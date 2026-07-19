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
| `data_quality_events` | `id` (indexes on `kind`, `ts`) | Pipeline-anomaly sink: `kind`, `subject`, `detail` JSON, nullable `source_id`, `resolved` flag. |
| `usage_events_v2` | PK `(provider, event_id)` | The v2 ledger: active attempt-event state flattened from the v2 wire model (finality/sequence, six token counters plus reasoning, success/outcome, routing/dimensions/extra JSON, trace ids). |
| `usage_event_revisions` | `id` (index `(provider, event_id)`) | Archive of superseded/conflicting/corrected states: `sequence`, `finality`, `payload` snapshot, `reason`, `actor`, `ts`. |
| `logical_requests` | PK `(provider, logical_request_id)` | Non-billable grouping of attempts: routing, `attempt_count`, `fallback_count`, `winning_attempt_id`, `ts_first`/`ts_last`. |
| `ingest_batches` | `batch_id` (index `received_at`) | Per-batch operational record: source id, token label, the five outcome counts, `schema_version`, `received_at`, `request_id`. Content-free. |
| `sources` | `id`, unique `(type, name, instance_id)` | Reporting-source registry: type, name, version, instance id, optional machine link, token label, `billing_mode`, first/last seen, `revoked`. Auto-registered from v2 payloads. |

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

**Backfill** (`services/registry_backfill.py`): a database that predates the
registries is reconciled once at startup, guarded by the
`registry_backfill_done` marker in `app_settings`. It scans distinct
`(provider, model)` pairs from `usage_events` and distinct providers from
`limit_snapshots`, registers providers, and inserts model rows -- recognized
Claude families as `active`, everything else as `unknown` with an
`unknown_model` data-quality record -- with `first_seen`/`last_seen` taken from
each model's min/max event timestamp. It never mutates usage rows
(FR-MODEL-007). Operators can re-run it for recovery with
`python -m tokemetry_server backfill-registries --force`.

### Data-quality events

`data_quality_events` (`services/data_quality.py`) is a sink for pipeline
anomalies -- `unknown_provider`, `unknown_model`, `schema_drift`,
`sequence_conflict`, `unpriced_usage`, `limit_source_failure`, `clock_skew` --
that should surface in the UI and alerts without failing ingest. It backs
FR-MODEL-006, FR-IDEMP-008, FR-PRICE-022, FR-LIMIT-013, and Epic TOK-9.

- **Burst dedup**: `record` keeps at most one open (`resolved=False`) row per
  `(kind, subject)` within `data_quality_dedup_window_seconds` (default one
  hour); a recurrence advances the open row's timestamp instead of inserting a
  new one. After the window lapses, or once resolved, a recurrence opens a
  fresh row so recurring issues stay visible over time.
- **Fire-and-forget**: ingest calls `record_safe`, which wraps the write in a
  SAVEPOINT and swallows any error, so a recording failure never rolls back
  accepted events (NFR-REL-008). Ingest records `unknown_provider` /
  `unknown_model` this way when the registry services observe them.
- `open_events` / `resolve_open` back the future `/api/v2/data-quality`
  endpoint and alert queries.

### v2 usage-event ledger

Migration `0006` adds the three tables that hold the v2 attempt-event lifecycle
(design Section 3.1, Epic TOK-3). They are additive: the physical `usage_events`
table is left untouched here and is not replaced by a compatibility view until
the backfill is verified (subtask 62.10).

- `usage_events_v2` holds the **active** state of each event, keyed by
  `(provider, event_id)` like v1 but carrying the full v2 shape: `schema_version`,
  `event_kind`, `finality`, `sequence`, the correlation ids, separate
  requested/routed/native models, three lifecycle timestamps, six `BigInteger`
  token counters plus `reasoning_tokens`, `success`/`outcome`, latency metrics,
  `provenance`, an integer `source_id` (a plain reference until Task 63 adds the
  `sources` table and its foreign key), and the `routing`/`dimensions`/`extra`/
  `tool_histogram` JSON columns and trace ids. Indexes cover the query and
  correlation dimensions: `ts_started`, `machine`, `session_id`, `native_model`,
  `logical_request_id`, `provider_request_id` (FR-EVENT-011), `outcome`,
  `source_id`, and the three trace ids.
- `usage_event_revisions` archives every superseded, conflicting, or corrected
  state (`reason` one of `superseded`/`conflict`/`correction`) with its `payload`
  snapshot, `actor`, and `ts`, indexed by `(provider, event_id)` for per-event
  history (FR-IDEMP-006). The revision engine (subtask 62.4) writes these.
- `logical_requests` groups the attempts of one logical request (D-003),
  populated from attempt events in subtask 62.11. No usage is stored here --
  only on the attempt rows (FR-EVENT-004).

### v1-to-v2 backfill (`db/backfill.py`, migration `0008`)

Migration `0008` copies every `usage_events` row into `usage_events_v2`
(`event_kind='attempt'`, `finality='final'`, `sequence=0`, the v1 `model` as
`native_model`, the five token counters copied with `reasoning_tokens=0`, and
the single v1 `ts` mapped onto both `ts_started` and `ts_completed`, per
FR-EVENT-023). V1-only columns with no v2 home (`git_branch`, `client_version`,
`entrypoint`, `is_sidechain`, `session_kind`, `speed`, `source`, `cost_usd`) are
preserved in `extra` under the `_v1` key so the v1 compatibility view (subtask
62.10) reproduces them; backfilled rows carry a `_backfill` marker so the
downgrade removes only them, never natively-ingested v2 rows.

The copy is chunked and keyset-paginated by `(provider, event_id)` and
idempotent (`ON CONFLICT DO NOTHING`), so it is resumable and never mutates
source rows. `verify_backfill` aggregates both tables in Python (dialect-
agnostic) by day/provider/machine and compares row counts, all five token sums,
and cost; any mismatch blocks the view swap. Operators can re-run or check out of
band with `python -m tokemetry_server backfill-usage-events` and
`python -m tokemetry_server verify-backfill` (the latter exits non-zero on a
mismatch and prints a machine-readable report).

### usage_events compatibility view (migration `0010`)

Once the backfill verifies, migration `0010` swaps `usage_events` for a
read-only view (D-001): it runs `verify_backfill` as a pre-check (aborting the
migration on any mismatch), renames the physical table to
`usage_events_v1_archive` (retained until the Task 70 retention policy), and
creates a view named `usage_events` selecting the active attempt rows of
`usage_events_v2` projected to the exact v1 column shape -- `ts` from
`ts_started`, `model` from `native_model`, `cost_usd` from the transitional
column, and the v1-only fields (`git_branch`, `is_sidechain`, ...) extracted
from `extra['_v1']`, with `extra` itself cleaned of the internal `_v1`/`_backfill`
keys. The view DDL is dialect-specific (SQLite JSON1 `json_extract`/`json_remove`;
Postgres `#>>`/`-`). From this point the v2 ledger is the sole store: v1 ingest
writes only through the revision engine, and all reads (services and Grafana)
flow through the view. The downgrade drops the view and renames the archive back.

## Migrations

Alembic migrations live in `db/migrations/`; `db/migrate.py` exposes
`upgrade_to_head(sync_url)` / `downgrade_to_base(sync_url)` for server
startup and tests. Alembic runs with a synchronous driver
(`postgresql+psycopg` or `sqlite`) derived from the async application URL by
`Settings.sync_database_url`. Migrations are hand-authored (through `0012`,
source health-tracking columns) and kept in sync with the ORM
by a drift test
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
