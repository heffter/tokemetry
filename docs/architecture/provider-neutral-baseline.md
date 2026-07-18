# Provider-Neutral Baseline (Migration Phase 0)

Authoritative audit of the current (v1) system, captured before any
provider-neutral schema change. Every statement here is cross-checked against
live code and Alembic migrations, not memory. All migration tasks in the
provider-neutral program (Tasks 61-71, epics TOK-2 through TOK-12) reference
this document.

- PRD: `.taskmaster/docs/tokemetry_ai_observability_prd.md` (PRD-TOK-002 v1.1),
  Section 10 and Epic TOK-1.
- Design spec: `docs/superpowers/specs/2026-07-12-provider-neutral-v2-design.md`.
- Scope of this epic: audit and documentation only. No production behavior
  changes (Migration Phase 0).

Load-bearing v1 behaviors that later epics MUST preserve are tagged inline as
**[V1-LOCK]** and consolidated in Section 4.

## Document status

| Section | Subtask | Status |
| --- | --- | --- |
| 1. Data model and dedupe semantics | 60.1 | Complete |
| 2. Ingest, query, and collector behavior | 60.2 | Complete |
| 3. Pricing, cost, rollups, and dashboard assumptions | 60.3 | Complete |
| 4. V1 compatibility contract and golden wire fixtures | 60.4 | Complete |
| 5. Migration constraints and test-to-epic mapping | 60.5 | Pending |

---

## 1. Data model and dedupe semantics

Source of truth for this section:

- ORM models: `apps/server/src/tokemetry_server/db/models.py`
- Base + cross-dialect types: `apps/server/src/tokemetry_server/db/base.py`
- Upserts: `apps/server/src/tokemetry_server/db/upsert.py`
- In-batch collapse: `apps/server/src/tokemetry_server/services/ingest.py`
- Core value objects: `packages/core/src/tokemetry_core/models.py`
- Claude Code parser (event_id formation): `packages/core/src/tokemetry_core/providers/claude_code.py`
- Schema DDL: `apps/server/src/tokemetry_server/db/migrations/versions/0001_initial_schema.py`,
  `0002_alert_dual_thresholds_state.py`, `0003_app_settings.py`

### 1.1 Cross-dialect column conventions

The schema is deliberately plain SQL (no DB-specific features beyond one JSON
column type) so Grafana can query the database directly (`models.py:1-7`).

- **Money**: `Numeric(20, 10)` (`_MONEY`, `models.py:41`) - 20 digits, 10
  fractional, for exact micro-USD arithmetic. Cache-read totals reach billions,
  so all token columns use `BigInteger`.
- **Tokens**: `BigInteger`, non-negative, default `0`.
- **JSON columns**: `JSONType = JSON().with_variant(JSONB(), "postgresql")`
  (`base.py:19`). Becomes `JSONB` on Postgres (indexable, typed) and generic
  `JSON` on SQLite. Migration 0001 mirrors this exactly with
  `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")` (`0001:22`).
- **Constraint naming convention** (`base.py:22-28`) makes Alembic names
  deterministic across dialects: `pk_%(table_name)s`,
  `uq_%(table_name)s_%(column_0_name)s`, `ix_%(column_0_label)s`,
  `fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`,
  `ck_%(table_name)s_%(constraint_name)s`. **[V1-LOCK]** these names appear in
  the migration history and must not be regenerated with different names.
- **Timestamps**: all `DateTime(timezone=True)`. Core value objects reject
  naive datetimes (`_require_tz`, `models.py:39-43`).

### 1.2 Tables

Ten tables. Columns listed with type, nullability, and default. "PK" = primary
key; indexes and unique constraints listed per table.

#### machines (`models.py:44-53`, DDL `0001:30-38`)

One row per collector-running machine.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | String(200) | no | **PK** (`pk_machines`) - machine name |
| `platform` | String(50) | yes | |
| `first_seen` | DateTime(tz) | yes | |
| `last_seen` | DateTime(tz) | yes | |
| `collector_version` | String(50) | yes | |

Upserted on `id`; conflict refreshes `platform`, `last_seen`,
`collector_version` only (`upsert.py:108-119`). `first_seen` is therefore set
on first insert and never overwritten by later touches.

#### usage_events (`models.py:56-83`, DDL `0001:40-70`)

One normalized provider usage record (typically one API request). The central
table of the system.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `provider` | String(50) | no | | **PK part 1** |
| `event_id` | String(200) | no | | **PK part 2** |
| `machine` | String(200) | yes | | indexed |
| `session_id` | String(200) | yes | | indexed |
| `ts` | DateTime(tz) | no | | indexed |
| `model` | String(200) | no | | indexed; stores the **native** model id |
| `project` | String(500) | yes | | |
| `git_branch` | String(300) | yes | | |
| `client_version` | String(50) | yes | | |
| `entrypoint` | String(50) | yes | | |
| `is_sidechain` | Boolean | no | `False` | |
| `session_kind` | String(50) | yes | | |
| `input_tokens` | BigInteger | no | `0` | |
| `output_tokens` | BigInteger | no | `0` | drives keep-max dedupe |
| `cache_read_tokens` | BigInteger | no | `0` | |
| `cache_write_short_tokens` | BigInteger | no | `0` | 5-minute cache |
| `cache_write_long_tokens` | BigInteger | no | `0` | 1-hour cache |
| `service_tier` | String(50) | yes | | |
| `speed` | String(50) | yes | | |
| `cost_usd` | Numeric(20,10) | yes | | NULL until costed |
| `provenance` | String(30) | no | | free string, not enum-constrained |
| `source` | String(50) | yes | | ingest writes `"collector"` |
| `extra` | JSONType | no | `{}` | provider-specific counters |

- **Composite PK `(provider, event_id)`** (`pk_usage_events`, `0001:65`). This
  is the natural idempotency key. **[V1-LOCK]**
- Indexes: `ix_usage_events_machine`, `ix_usage_events_session_id`,
  `ix_usage_events_ts`, `ix_usage_events_model` (`0001:67-70`).
- **`model` stores the native model id**: ingest maps core
  `event.native_model` -> DB column `model` (`ingest.py:155`). There is no
  separate requested/routed model column in v1. **[V1-LOCK]** (PRD FR-EVENT-012
  splits requested/routed/native in v2.)
- `source` is hardcoded to `"collector"` for events written by the ingest path
  (`ingest.py:171`).

#### limit_snapshots (`models.py:86-99`, DDL `0001:72-88`)

Utilization of one provider limit window at one point in time. Append-only.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | no | **PK**, autoincrement |
| `provider` | String(50) | no | indexed |
| `machine` | String(200) | yes | indexed |
| `ts` | DateTime(tz) | no | indexed |
| `window_kind` | String(50) | no | indexed; opaque provider label |
| `utilization_pct` | Numeric(7,3) | no | |
| `resets_at` | DateTime(tz) | yes | |
| `provenance` | String(30) | no | |
| `raw` | JSONType | no | original payload preserved |

- No unique constraint: snapshots are appended, not upserted
  (`ingest.py:108-121` uses `add_all`). **[V1-LOCK]** (append semantics)
- `window_kind` is an opaque, provider-defined label. Anthropic emits
  `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`,
  `extra_credits` (`core/models.py:141-144`). Consumers treat it as opaque so
  new providers need no schema change.

#### sessions (`models.py:102-120`, DDL `0001:90-110`)

Per-session aggregate.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `session_id` | String(200) | no | **PK** |
| `provider` | String(50) | no | indexed |
| `machine` | String(200) | yes | indexed |
| `project` | String(500) | yes | |
| `slug` | String(300) | yes | |
| `started_at` | DateTime(tz) | yes | indexed |
| `last_at` | DateTime(tz) | yes | |
| `message_count` | Integer | no | default 0 |
| `input_tokens`..`cache_write_long_tokens` | BigInteger | no | default 0 |
| `cost_usd` | Numeric(20,10) | yes | |

- **The `sessions` table exists but is NOT populated by the ingest path.**
  `IngestService` writes machines, usage_events, limit_snapshots, and
  daily_rollups only - it never inserts into `sessions` (`ingest.py`, no
  `models.Session` reference). Session-level views derive from `usage_events`
  at query time. **[V1-LOCK]**: the table is part of the v1 schema and must
  survive migration even though it is currently dormant.

#### daily_rollups (`models.py:123-150`, DDL `0001:112-133`)

Per-day, per-grain token and cost totals for fast history queries.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | Integer | no | | **PK**, autoincrement |
| `day` | Date | no | | indexed |
| `provider` | String(50) | no | | grain |
| `machine` | String(200) | no | `""` | grain, `''` sentinel |
| `model` | String(200) | no | `""` | grain, `''` sentinel |
| `project` | String(500) | no | `""` | grain, `''` sentinel |
| `input_tokens`..`cache_write_long_tokens` | BigInteger | no | `0` | |
| `total_tokens` | BigInteger | no | `0` | |
| `cost_usd` | Numeric(20,10) | yes | | |
| `provenance` | String(30) | no | `"derived"` | ORM default |

- **Unique grain `(day, provider, machine, model, project)`**
  (`daily_rollups_grain`, `models.py:131-135`, `0001:129-131`). **[V1-LOCK]**
- **`''` (empty-string) sentinels for absent `machine`/`model`/`project`** keep
  the unique constraint identical on SQLite and Postgres (NULLs would not
  collide in a unique index). Bootstrap rows hardcode `project=""`
  (`ingest.py:181`). **[V1-LOCK]**: sentinel convention is load-bearing for
  upsert determinism across dialects.
- ORM `provenance` default is `"derived"`, which is **not** a member of the
  core `Provenance` enum (see 1.4). Bootstrap-imported rows instead store
  `str(aggregate.provenance)` = `"stats_cache"` (`ingest.py:190`).

#### pricing (`models.py:153-170`, DDL `0001:135-149`)

Date-versioned per-MTok prices for a provider model.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | Integer | no | | **PK**, autoincrement |
| `provider` | String(50) | no | | grain |
| `model` | String(200) | no | | grain |
| `effective_date` | Date | no | | grain |
| `input_per_mtok` | Numeric(20,10) | no | | |
| `output_per_mtok` | Numeric(20,10) | no | | |
| `cache_read_per_mtok` | Numeric(20,10) | no | | |
| `cache_write_short_per_mtok` | Numeric(20,10) | no | | |
| `cache_write_long_per_mtok` | Numeric(20,10) | no | | |
| `source` | String(50) | no | `"litellm"` | |

- **Unique grain `(provider, model, effective_date)`** (`pricing_grain`,
  `models.py:157-159`, `0001:148`). Cost is computed with the latest row whose
  `effective_date` is not after the event timestamp (`core/models.py:197-203`).
  Detailed pricing/cost audit is in Section 3 (subtask 60.3).

#### alert_rules (`models.py:173-197`, DDL `0001:151-165` + `0002:25-33`)

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | Integer | no | | **PK**, autoincrement |
| `name` | String(200) | no | | unique (`uq_alert_rules_name`) |
| `kind` | String(50) | no | | |
| `threshold` | Numeric(12,4) | yes | | legacy single threshold |
| `warn_threshold` | Numeric(12,4) | yes | | added by migration 0002 |
| `crit_threshold` | Numeric(12,4) | yes | | added by migration 0002 |
| `window_kind` | String(50) | yes | | |
| `channels` | JSONType | no | `[]` | list of channel names |
| `cooldown_seconds` | Integer | no | `3600` | |
| `quiet_hours` | JSONType | yes | | |
| `enabled` | Boolean | no | `True` | |
| `config` | JSONType | no | `{}` | |
| `state` | String(20) | no | `"normal"` | added by 0002 (server_default) |
| `last_fired_at` | DateTime(tz) | yes | | added by migration 0002 |

- Migration 0002 (`0002_alert_dual_thresholds_state.py`) adds
  `warn_threshold`, `crit_threshold`, `state` (server_default `"normal"`), and
  `last_fired_at`. Legacy single `threshold` is kept for compatibility;
  warn/crit take priority when set.

#### alert_events (`models.py:200-214`, DDL `0001:167-183`)

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | no | **PK**, autoincrement |
| `rule_id` | Integer (FK -> alert_rules.id) | no | indexed; `fk_alert_events_rule_id_alert_rules` |
| `ts` | DateTime(tz) | no | indexed |
| `severity` | String(20) | no | |
| `title` | String(300) | no | |
| `body` | String(2000) | no | |
| `delivered` | Boolean | no | default False |
| `context` | JSONType | no | default `{}` |

#### api_tokens (`models.py:217-227`, DDL `0001:185-197`)

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | no | **PK**, autoincrement |
| `label` | String(200) | no | unique (`uq_api_tokens_label`) |
| `token_hash` | String(128) | no | unique + indexed (`uq_api_tokens_token_hash`, `ix_api_tokens_token_hash`) |
| `created_at` | DateTime(tz) | no | |
| `last_used` | DateTime(tz) | yes | |
| `revoked` | Boolean | no | default False |

#### app_settings (`models.py:230-242`, DDL `0003_app_settings.py`)

Runtime key/value settings (UI-editable channel secrets, etc.). Added by
migration 0003.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `key` | String(100) | no | | **PK** |
| `value` | String(2000) | no | `""` (server_default) | stored as string, coerced on read |
| `updated_at` | DateTime(tz) | no | | |

### 1.3 Idempotency and dedupe semantics

Deduplication happens at **three** layers. All three key on
`(provider, event_id)` and resolve ties by **keeping the maximum
`output_tokens`**. This is the single most load-bearing v1 behavior.

1. **Parser layer** (`claude_code.py:87-126`). One logical API request can emit
   several JSONL lines sharing a `requestId` (streaming snapshots followed by
   the final record). Within a single parse pass, the entry with the largest
   `output_tokens` wins (last one on ties): `event.output_tokens >=
   current.output_tokens` (`claude_code.py:119`). Keeping the first entry would
   undercount output up to ~5x (`claude_code.py:8-14`).

2. **In-batch collapse** (`ingest.py:34-50`, `_dedupe_keep_max`). Before upsert
   a batch must not target the same conflict key twice - Postgres rejects a
   statement that updates the same row twice (`upsert.py:8-10`). The service
   collapses duplicates in-batch keeping max output, preserving first-seen
   order for determinism (`ingest.py:40-50`). The result count feeds
   `duplicates_merged = len(events) - len(deduped)` (`ingest.py:96-98`).

3. **Cross-batch upsert** (`upsert.py:73-88`, `usage_events_upsert`). Dialect-
   aware `INSERT ... ON CONFLICT (provider, event_id) DO UPDATE` where the
   existing row is overwritten **only when** `excluded.output_tokens >=
   usage_events.output_tokens` (`upsert.py:87`). A later streaming snapshot with
   fewer output tokens is therefore ignored; the settled (largest) record wins
   and survives replays. Replaying an identical event is a no-op net effect.

**event_id formation** (`claude_code.py:186`): `event_id = record["requestId"]
or message["id"]`. The Claude Code transcript `requestId` is the primary
identity; `message.id` is the fallback when `requestId` is absent. A record
with neither is skipped (returns `None`, `claude_code.py:186-188`).
**[V1-LOCK]** - this exact precedence and the keep-max rule are what
FR-IDEMP-012 ("historical v1 keep-max behavior MUST remain unchanged on v1
endpoints") and FR-EVENT-023 ("V1 events MUST map into v2 with
`event_kind = 'attempt'` and compatibility defaults") require preserving.

On-conflict update column sets are explicit allow-lists (everything but the PK
/ grain):

- usage_events: `_EVENT_UPDATE_COLUMNS` (`upsert.py:27-49`) - 21 columns, all
  non-PK columns.
- daily_rollups: `_ROLLUP_UPDATE_COLUMNS` (`upsert.py:52-61`) - the 6 token/cost
  columns + `provenance`; the row is **replaced, not accumulated**, so
  recomputing a day converges to the same totals (`upsert.py:94-99`).
- pricing: `_PRICING_UPDATE_COLUMNS` (`upsert.py:122-130`).

### 1.4 Provenance

Core enum `Provenance(StrEnum)` (`core/models.py:20-30`) has **three** values:

- `official` = `"official"` - authoritative provider endpoint.
- `local_estimate` = `"local_estimate"` - derived from local artifacts
  (transcripts); default for parsed `UsageEvent`s (`core/models.py:75`,
  `claude_code.py:232`).
- `stats_cache` = `"stats_cache"` - imported once from a provider's local
  aggregate cache; default for `DailyAggregate` (`core/models.py:115`,
  `claude_code.py:163`).

**Important schema fact:** the DB `provenance` columns are free `String(30)`,
**not** enum-constrained. `daily_rollups.provenance` even carries an ORM default
of `"derived"` (`models.py:150`) - a value outside the enum, used for
event-derived rollups (confirmed in Section 3). Provenance is written to the DB
via `str(provenance)` (`ingest.py:170,190`). **[V1-LOCK]**: v2 must not narrow
this column to a CHECK/enum that would reject existing `"derived"` rows without
a data migration. (PRD FR-EVENT-025 additionally wants `imported` and
`adjusted`.)

### 1.5 Core value objects vs storage (name mapping)

The core Pydantic models (`packages/core`) are frozen, `extra="forbid"`,
strictly validated (`core/models.py:33-36`). Key mismatches between the core
model field names and the DB column names - preserved by the ingest mapping and
therefore part of the v1 contract:

| Core field (`core/models.py`) | DB column (`db/models.py`) | Mapped at |
| --- | --- | --- |
| `UsageEvent.native_model` | `usage_events.model` | `ingest.py:155` |
| `DailyAggregate.native_model` | `daily_rollups.model` | `ingest.py:184` |
| (n/a - hardcoded) | `usage_events.source = "collector"` | `ingest.py:171` |
| (n/a - hardcoded) | `daily_rollups.project = ""` | `ingest.py:181` |

Token counters are validated `ge=0` in core (`core/models.py:68-72`).
`UsageEvent.total_tokens` is a computed property (sum of the 5 categories,
`core/models.py:84-93`); `DailyAggregate.total_tokens` is a stored field that
defaults to the sum of split fields when zero (`core/models.py:117-134`).

---

## 2. Ingest, query, and collector behavior

Source of truth: `api/schemas.py`, `api/ingest.py`, `api/auth.py`, `api/deps.py`,
`api/stream.py`, `api/query.py`, `services/validation.py`,
`services/broadcast.py`, `config.py`, and `apps/collector/`.

### 2.1 Ingest endpoints (`api/ingest.py`, `api/schemas.py`)

Router prefix `/api/v1/ingest` (`ingest.py:22`). Three POST routes, all
authenticated (`Depends(require_token)`) and all-or-nothing transactional (the
request-scoped session commits on success, rolls back on any exception -
`deps.py:17-26`).

| Endpoint | Body model | Batch bound | Publishes to WS? |
| --- | --- | --- | --- |
| `POST /api/v1/ingest/events` | `EventsIngest` | `events`: **1-5000** | yes (`{type: events}`) |
| `POST /api/v1/ingest/limits` | `LimitsIngest` | `snapshots`: **1-1000** | yes (`{type: limits}`) |
| `POST /api/v1/ingest/bootstrap` | `BootstrapIngest` | `aggregates`: **1-20000** | no |

- Batch bounds are `Field(min_length=1, max_length=N)` on the list
  (`schemas.py:153,162,171`). **[V1-LOCK]** exact caps 5000/1000/20000.
- Each wire model has `model_config = ConfigDict(extra="forbid")`
  (`schemas.py:27,37,90,117,150,159,168`): **unknown JSON fields are rejected**
  (422). **[V1-LOCK]** - v2 additive fields must remain optional and the v1
  models stay strict, so a v1 collector's payload is still accepted verbatim.
- Wire -> core conversion via `to_core(machine)` stamps the machine name onto
  each record (`schemas.py:60,100,130`). Wire field `native_model` maps to core
  `native_model` -> DB `model` (see Section 1.5).
- **Wire field inventory** (the frozen v1 request contract):
  - `MachineInfo`: `name` (1-200, required), `platform` (<=50), `collector_version`
    (<=50).
  - `UsageEventIn`: `event_id` (1-200), `provider` (1-50), `native_model` (1-200),
    `ts`, `session_id`, `project` (<=500), `git_branch` (<=300), `client_version`,
    `entrypoint`, `is_sidechain` (default False), `session_kind`, the 5 token
    counters (`ge=0`, default 0), `service_tier`, `speed`, `provenance`
    (default `local_estimate`), `extra` (dict, default `{}`).
  - `LimitSnapshotIn`: `provider`, `ts`, `window_kind` (1-50), `utilization_pct`
    (`ge=0.0`), `resets_at` (optional), `provenance` (default `official`), `raw`
    (dict).
  - `DailyAggregateIn`: `provider`, `day`, `native_model`, the 5 token counters,
    `total_tokens`, `message_count`. **Note: no `provenance` field** on the wire
    bootstrap model (unlike events/limits); the server assigns `stats_cache` via
    the core default.
- **Response** `IngestResult`: `{accepted: int, duplicates_merged: int = 0}`
  (`schemas.py:174-178`). Only the events path sets `duplicates_merged`
  (`ingest.py`/`services/ingest.py:96-98`); limits/bootstrap return
  `duplicates_merged = 0`. **[V1-LOCK]** response shape.
- **Error contract** (`ingest.py:1-5,43-44`): malformed batch (schema/type) ->
  **422** (FastAPI/Pydantic); sanity-check failure (`ValidationError`) -> **400**.
  Bootstrap does not run sanity validation (`ingest.py:72-80`).

### 2.2 Validation caps (`services/validation.py`)

Cross-field sanity checks beyond Pydantic bounds, applied per event/snapshot in
`IngestService.ingest_events`/`ingest_limits` (`ingest.py:80-82,104-106`):

- `_MAX_TOKENS = 10_000_000_000` (10 billion): any single token counter above
  this rejects the **whole batch** (`validation.py:17,45-46`).
- `_MAX_CLOCK_SKEW = timedelta(hours=2)`: a `ts` more than 2h in the future is
  rejected (`validation.py:21,47-48,57-58`).
- `_MAX_UTILIZATION = 1000.0`: a limit `utilization_pct` above 1000 is rejected
  (`validation.py:24,53-55`).
- Any failure raises `ValidationError` (a `ValueError`), surfaced as HTTP 400,
  rolling back the batch. **[V1-LOCK]** exact caps and whole-batch rejection.

### 2.3 Authentication (`api/auth.py`, `api/stream.py`, `config.py`)

- Every `/api/v1` route (ingest and query) depends on `require_token`
  (`auth.py:40`). HTTP bearer via `HTTPBearer(auto_error=False)`.
- A token is authorized if it matches **either** (a) the configured bootstrap
  token, compared constant-time with `hmac.compare_digest`
  (`auth.py:32-37,54-55`), returning caller label `"bootstrap"`; **or** (b) a
  non-revoked `api_tokens` row matched by `hash_token(token)`; a DB match
  refreshes `last_used` (`auth.py:57-79`). Missing/invalid -> **401** with
  `WWW-Authenticate: Bearer`.
- Bootstrap token env var: **`TOKEMETRY_API_BOOTSTRAP_TOKEN`** (settings
  `env_prefix="TOKEMETRY_"` + field `api_bootstrap_token`, `config.py:27,45`).
- **WebSocket auth** (`stream.py`): `GET /api/v1/stream` authenticates via a
  `?token=` **query parameter** (browsers cannot set an Authorization header on
  WS handshakes). Same bootstrap-or-DB-token check (`stream.py:24-40`); failure
  closes the socket with code **1008** (policy violation). **[V1-LOCK]** WS auth
  mechanism.

### 2.4 Query endpoints (`api/query.py`, `services/queries.py`)

Router prefix `/api/v1` (`query.py:53`). **16 read endpoints, all authenticated**
(`require_token` on every one - verified 16/16). Listed in router order to make
later diffing mechanical:

| # | Endpoint | Filters / params | Response |
| --- | --- | --- | --- |
| 1 | `GET /summary/now` | (none; uses server now) | `SummaryNow` |
| 2 | `GET /summary/overview` | (none) | `OverviewOut` |
| 3 | `GET /limits/current` | (none) | `list[LimitOut]` |
| 4 | `GET /limits/history` | `window_kind` (req, >=1), `hours` (24, 1-720) | `list[LimitOut]` |
| 5 | `GET /blocks` | `hours` (120, 5-2400) | `list[BlockOut]` |
| 6 | `GET /usage` | `group_by` ("day"), `from`/`to` (date), `provider`, `machine`, `model`, `project` | `UsageResponse` |
| 7 | `GET /sessions` | `limit` (100, 1-1000) | `list[SessionOut]` |
| 8 | `GET /sessions/{session_id}` | path `session_id`; 404 if unknown | `SessionDetailOut` |
| 9 | `GET /report` | `from`/`to` | `ReportOut` |
| 10 | `GET /report/export` | `size` (`compact`\|`full`), `from`/`to` | (export payload) |
| 11 | `GET /insights/anomalies` | (none) | `AnomalyReportOut` |
| 12 | `POST /admin/rebuild-rollups` | (admin; recomputes rollups) | `RebuildResult` |
| 13 | `GET /machines` | (none) | `list[MachineOut]` |
| 14 | `GET /heatmap` | `from`/`to`, `machine`, `project` | `HeatmapResponse` |
| 15 | `GET /cost` | `from`/`to` | `CostResponse` |
| 16 | `GET /pricing` | (none) | `list[PricingOut]` |

- The full filter dimension set the API exposes today is **`provider`, `machine`,
  `model`, `project`** (only `/usage` accepts all four; `/heatmap` accepts
  `machine`, `project`), plus **`from`/`to`** date range (aliased from
  `date_from`/`date_to`, `query.py:223-224` etc.) and **`window_kind`** for
  limits history. There is **no `?session=` query filter**; session scoping is
  via the `/sessions/{session_id}` path only. **[V1-LOCK]** these query
  parameter names and aliases are the dashboard's contract.
- `from`/`to` default via `_default_range` when omitted (`query.py:59-66`).
- `/cost` computes a subscription "value multiple" from
  `subscription_monthly_usd` prorated over the range (`query.py:463-478`) -
  detailed in Section 3.

### 2.5 WebSocket broadcast contract (`services/broadcast.py`, `api/ingest.py`)

- In-process, single-process pub/sub. Each subscriber gets a bounded asyncio
  queue of size `_QUEUE_SIZE = 100` (`broadcast.py:17,38`). A subscriber whose
  queue fills is **dropped** rather than blocking ingest (`broadcast.py:27-33`) -
  the stream is best-effort, not durable.
- Messages published by ingest: events -> `{"type": "events", "machine": <name>,
  "accepted": <int>}`; limits -> `{"type": "limits", ...}` (`ingest.py:45-48,
  65-68`). Bootstrap publishes nothing. **[V1-LOCK]** message shape.

### 2.6 Collector behavior (`apps/collector/`)

Source: `state.py`, `runner.py`, `sources.py`, `uploader.py`, `config.py`,
`limits_anthropic.py`, `wire.py`, `cli.py`, and the core parser
`packages/core/.../providers/claude_code.py`.

#### 2.6.1 SQLite state DB (`state.py`)

One SQLite file per collector install (path `state_db_path`); machine identity
is implicit in "which file this is" - there is no machine column. Three tables
(exact DDL, `state.py:18-36`):

- `file_offsets(source TEXT, path TEXT, offset INTEGER, size INTEGER, PRIMARY
  KEY (source, path))` - byte offset + last-seen size per source/file.
- `upload_queue(id INTEGER PK AUTOINCREMENT, kind TEXT, payload TEXT, attempts
  INTEGER DEFAULT 0)` - `kind` in `{events, limits, bootstrap}`; `payload` is the
  full batch dict as a JSON string.
- `meta(key TEXT PRIMARY KEY, value TEXT)` - only key used today is
  `bootstrap_done` = `"1"`.

**No PRAGMAs** (no WAL, no synchronous tuning, no FK enforcement) - stock SQLite
defaults. Each mutating method commits immediately (per-call autocommit);
`file_offsets`/`meta` use `INSERT ... ON CONFLICT DO UPDATE`.

#### 2.6.2 Byte-offset tailing + truncation detection (`runner.py`, parser)

- Offset keyed by `source_key = f"{provider}:{ClassName}"` (e.g.
  `"anthropic:ClaudeCodeJsonlSource"`, `runner.py:156`) and file path string.
- New files start at offset 0. **Truncation/rotation detection**: `if stored is
  not None and file.size < stored.offset: offset = 0` (`runner.py:162-163`) -
  re-read from start. (Compares current size to stored **offset**, not stored
  size.)
- `new_offset` is persisted **after** the file's events are enqueued
  (`runner.py:179-180`); enqueue and `set_offset` are **separate commits**, not
  one atomic transaction. Safety is at-least-once: a crash between them re-parses
  the same bytes, which the server's `(provider, event_id)` keep-max dedupe makes
  idempotent (Section 1.3). **[V1-LOCK]** the offset/dedupe safety model.
- Trailing incomplete (no-newline) line is not consumed; `new_offset` stays at
  its start so the next pass re-reads it complete (`claude_code.py:97-99`).

#### 2.6.3 Upload queue + drain semantics (`uploader.py`, `runner.py`)

- Enqueue: events chunked by `upload_batch_size` (default 500), one queue row
  per chunk; limits and bootstrap are one row each (not chunked).
- Drain (`runner.py:200-213`) is strictly FIFO by `id`, **one row per HTTP POST**
  (`pending(1)`). A row is deleted (`mark_uploaded`) **only after a 2xx response**
  (`Uploader.send` returns `response.is_success`, `uploader.py:64-65`). On any
  non-2xx or network error, `attempts` is incremented and the drain loop
  **breaks** for the cycle.
- Endpoint map (`uploader.py:16-20`): `events -> /api/v1/ingest/events`,
  `limits -> /api/v1/ingest/limits`, `bootstrap -> /api/v1/ingest/bootstrap`.
  Auth header `Authorization: Bearer <api_token>`.
- **Known gaps (not [V1-LOCK]; candidates for the migration to improve, but must
  be preserved as observable behavior unless a task changes them):** the
  `attempts` counter is incremented but never read - no max-retry cutoff, no
  backoff/jitter (retries gated only by the poll interval), and **head-of-line
  blocking**: a permanently-rejected row (e.g. a 400/422) blocks the whole FIFO
  queue every cycle. No dead-letter path.

#### 2.6.4 bootstrap_done guard (`runner.py`)

One-time historical import guarded by `meta["bootstrap_done"] == "1"`
(`runner.py:34,105-106`). When unset, each source's `bootstrap()` aggregates are
enqueued as a `bootstrap` batch, then the flag is set - **unless `--dry-run`**,
which computes but never enqueues and never sets the flag (safe to re-run).

#### 2.6.5 OAuth limits polling (`limits_anthropic.py`) - Anthropic-specific

- Endpoint: `https://api.anthropic.com/api/oauth/usage`
  (`ANTHROPIC_API_BASE + OAUTH_USAGE_PATH`, `limits_anthropic.py:27,30,130`).
  This is an **undocumented** endpoint.
- Auth: OAuth bearer read from Claude Code's `~/.claude/.credentials.json` via a
  recursive search for `accessToken`/`access_token` (`limits_anthropic.py:42-72`).
- Headers spoof the Claude CLI to avoid rate limiting: `anthropic-beta:
  oauth-2025-04-20`, `User-Agent: claude-cli/2.1 (external, tokemetry-collector)`
  (`limits_anthropic.py:33,36,131-135`).
- Window kinds emitted: `_WINDOW_KEYS = ("five_hour", "seven_day",
  "seven_day_opus", "seven_day_sonnet")` (`limits_anthropic.py:39`).
  **Discrepancy to record:** this **omits `extra_credits`**, which the core
  `LimitSnapshot` docstring lists as an Anthropic window kind
  (`core/models.py:141-144`). Later epics must not assume the documented set is
  exhaustive of what the collector actually emits.
- `utilization_pct` is passed through as-is from `window["utilization"]` (no
  scaling); `resets_at` parsed from epoch or ISO-8601; `provenance = OFFICIAL`;
  `raw` preserves the window dict. Polled first cycle then every
  `limits_poll_interval_seconds` (default 120).

#### 2.6.6 Collector TOML config (`config.py`)

`CollectorConfig` is `extra="forbid"`; per-source `SourceConfig` is `extra="allow"`
(so `claude_home` is an ad-hoc extra key, not a declared field).

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `server_url` | str | **required** | server base URL |
| `api_token` | str | **required** | bearer token |
| `machine_name` | str | **required** | stamped into every payload |
| `machine_platform` | str | `platform.system()` | |
| `poll_interval_seconds` | float | `60.0` | daemon cycle |
| `limits_poll_interval_seconds` | float | `120.0` | limits cadence |
| `upload_batch_size` | int | `500` | events per queue row |
| `state_db_path` | Path | `tokemetry-collector-state.sqlite3` | |
| `sources` | dict[str, SourceConfig] | `{}` | `[sources.<name>]` |
| `limits` | dict[str, SourceConfig] | `{}` | `[limits.<name>]` |

Registered names live in `sources.py` (`claude_code`, `anthropic_oauth`), not in
`config.py` - the config schema itself is provider-neutral.

#### 2.6.7 Wire format (`wire.py`) - the upload contract

The collector emits plain JSON dicts matching the server ingest schemas; the
machine envelope is attached once per batch (`machine.name` is stamped onto each
row server-side). **[V1-LOCK]** the entire wire format below - it is the exact
mirror of the server request schemas (Section 2.1) and must stay accepted verbatim.

- `machine_info`: `{name, platform, collector_version}` (version `"0.1.0"`,
  `__init__.py:10`).
- `event_to_wire` (`wire.py:27-50`): all `UsageEventIn` fields; `ts.isoformat()`;
  `provenance = str(...)`. **Omits the computed `total_tokens`** (server
  recomputes).
- `limit_to_wire` (`wire.py:53-63`): `provider, ts, window_kind, utilization_pct,
  resets_at, provenance, raw`.
- `aggregate_to_wire` (`wire.py:66-79`): `provider, day, native_model`, 5 token
  counters, `total_tokens`, `message_count`. **Omits `provenance`** (server
  assigns `stats_cache` by default - matches the wire `DailyAggregateIn` model
  which also has no provenance field, Section 2.1).
- Batch envelopes: `{"machine": ..., "events": [...]}` /
  `{"machine": ..., "snapshots": [...]}` / `{"machine": ..., "aggregates": [...]}`.

#### 2.6.8 CLI (`cli.py`)

Single flat command `tokemetry-collector` (no subcommands). Flags: `--config
PATH` (required), `--once` (one cycle, exit), `--dry-run` (parse/report, no state
change or upload; **implies run-once**), `--bootstrap` (one-time import before
collecting; additive, not a separate mode). `finally` always closes uploader and
state.

## 3. Pricing, cost, rollups, and dashboard assumptions

Source: `packages/core/.../pricing/{table,anthropic,litellm}.py`, `registry.py`,
`services/{cost,pricing_repo,litellm_sync,rollups,report}.py`, `api/pricing.py`,
`app.py`, and `apps/dashboard/src/`.

Epic pointers used below: **TOK-5** = Pricing Rate Card (task 64), **TOK-7** =
Query API v2 (task 66), **TOK-8** = Dashboard Generalization (task 67),
**TOK-12** = Multi-Provider Limits (task 69).

### 3.1 PriceRow and effective-date resolution (`pricing/table.py`)

`PriceRow` (`core/models.py:197-212`) carries five per-MTok `Decimal` rates
(`ge=0`): `input_per_mtok`, `output_per_mtok`, `cache_read_per_mtok`,
`cache_write_short_per_mtok`, `cache_write_long_per_mtok`, plus `provider`,
`model`, `effective_date`.

`PricingTable` (`pricing/table.py`) stores rows in `dict[(provider, model),
list[PriceRow]]` sorted by `effective_date`. `resolve(provider, model, on)`
tries candidates in order and returns the first hit, else raises
`UnknownModelError`:

1. Exact `(provider, model)`.
2. If `model` has a `-YYYYMMDD` suffix (`_DATE_SUFFIX = r"-\d{8}$"`,
   `base_model_id()` strips it): also try `(provider, <base>)` - a **dated query
   falls back to the undated base row**.
3. If `model` is undated: search all same-provider rows whose `base_model_id`
   equals `model`, **sorted by model id descending (lexicographic)** - an undated
   query resolves to the newest matching dated snapshot id.

Within a candidate, `_latest_not_after(on)` returns the row with the greatest
`effective_date <= on` (correct historical pricing), or `None` if the earliest
known price is still in the future. `apply_overrides()` may override **only the
five money fields** (`_OVERRIDABLE_FIELDS`); non-price fields raise. Rows are
frozen; overrides return copies. **[V1-LOCK]** the date-suffix fallback and
latest-not-after semantics (historical costs must stay stable). -> TOK-5.

### 3.2 Cost computation, engine wiring, and recompute

**Cost formula** (`pricing/anthropic.py:39-46`) - **verified against source and
`test_pricing.py`**: a five-term dot product of token counts and per-MTok rates,
divided by `_MTOK = Decimal(1_000_000)`, then quantized to
`_CENT_MICRO = Decimal("0.000001")` (micro-USD, 6 dp, banker's rounding):

```
cost = (input*input_rate + cache_write_short*cws_rate + cache_write_long*cwl_rate
        + cache_read*cr_rate + output*output_rate) / 1_000_000
cost = cost.quantize(Decimal("0.000001"))
```

`input_tokens` is already the **uncached** count for Anthropic (no subtraction).
**[V1-LOCK]** formula, term set, and micro-USD quantization.

**`DEFAULT_ANTHROPIC_PRICE_ROWS`** (`anthropic.py:64-72`): 4 rows, all
`effective_date = 2026-01-01`, cache rates derived from input price
(read x0.1, short-write x1.25, long-write x2): `claude-opus-4-5` $5/$25,
`claude-opus-4-1` $15/$75, `claude-sonnet-4-5` $3/$15, `claude-haiku-4-5` $1/$5.
This Anthropic price data lives in the supposedly provider-neutral **core**
package. -> TOK-5.

**`ProviderRegistry`** (`registry.py`): pricing strategies are registered by
`strategy.provider` and looked up by provider string (`UnknownProviderError` on
miss). The server's `build_registry()` (`providers.py:14-18`) registers **only
`AnthropicPricingStrategy`** - any other provider's events always fall to the
unknown path. -> TOK-5.

**Engine wiring** (`services/cost.py`, `app.py:41-49,90-108`): `CostEngine(table,
registry)`; `app.py._build_cost_engine` seeds default pricing, loads the table,
and builds the engine. `app.state.cost_fn = engine.cost` is injected into
`IngestService` via `deps.py:37`. Cost is computed **once at ingest** and stored
in `usage_events.cost_usd` (`ingest.py:148,169`).

**Unknown-model -> NULL cost -> alert**: `CostEngine.cost` returns `None` (logs
once, accumulates `(provider, model)` in `_unknown_models`) when `resolve` or
`registry.pricing` raise (`cost.py:39-53`). NULL `cost_usd` feeds the
`unknown_model` alert evaluator, which counts `usage_events` in the last day with
`cost_usd IS NULL` and fires "Unpriced usage"
(`services/alerting/rules.py:155-183`). **[V1-LOCK]** unknown-model -> NULL cost
(never a guessed/zero cost).

**Recompute** (`api/pricing.py`, prefix `/api/v1/pricing`, all `require_token`):
`POST /` adds a price row; `POST /sync-litellm` syncs from LiteLLM
(`_SYNC_EFFECTIVE_DATE = 2025-01-01`); `POST /recompute` builds a fresh
`CostEngine`, reprices **all** events, refreshes **all** rollup days, and
**hot-swaps** `app.state.cost_fn = engine.cost` (no restart). Note: any valid
bearer token can call these - there is no separate admin scope. -> TOK-5.

### 3.3 LiteLLM import and fallback multipliers

`pricing/litellm.py` transforms LiteLLM's price map into `PriceRow`s. Fallback
cache multipliers, applied **relative to the base input price** - **verified**:
`_CACHE_READ_MULTIPLIER = 0.1`, `_SHORT_WRITE_MULTIPLIER = 1.25`,
`_LONG_WRITE_MULTIPLIER = 2` (`litellm.py:25-27`). Source cache prices are used
when present, else the multiplier is applied. Tests confirm long-write is always
`input x 2` even when short-write is explicit (`test_pricing.py:88-91`).

`price_rows_from_litellm` filters `litellm_provider == provider` (default
`"anthropic"`) and skips ids containing `.` or `/` (platform-prefixed aliases).
`services/litellm_sync.py`: `LITELLM_PRICES_URL` points at the BerriAI GitHub raw
JSON; fetch timeout 30s; `sync_anthropic_pricing` **hardcodes
`provider="anthropic"`**, `source="litellm"`. LiteLLM sync is Anthropic-only
end-to-end. -> TOK-5.

### 3.4 Rollups (`services/rollups.py`)

- **Grain** `(day, provider, machine, model, project)` (`_aggregate_day`); rows
  written with **`provenance = "derived"`** (`DERIVED`, `rollups.py:26,180`) -
  the value outside the `Provenance` enum flagged in Section 1.4. **[V1-LOCK]**.
- **`''` sentinels**: `machine` and `project` are `coalesce(col, "")` at the SQL
  level before grouping (`rollups.py:127-128`); `model` is not coalesced
  (`native_model` is non-null). Matches the unique-grain requirement (Section
  1.2 daily_rollups).
- **Project-group folding**: each raw project is folded via `project_group(...,
  roots)` (`rollups.py:160`) and rows folding to the same group are summed
  (tokens element-wise, costs via `_add_cost` where both-None stays None). The
  folding rule lives in `core/projects.py`, whose worktree regex
  (`.claude/worktrees/<name>`) is **Claude-Code-specific**.
- **Whole-day recompute**: `refresh_rollups_for_days` recomputes each day in full
  (not a delta) and upserts, so it converges under keep-max event updates. Called
  per-batch for touched days (`ingest.py:91-93`) and for all event days on
  `/recompute` (`api/pricing.py`). `rebuild_all_rollups` deletes all
  `provenance == "derived"` rows and recomputes every day (needed after
  project-grouping rule changes). **[V1-LOCK]** replace-not-accumulate semantics.

### 3.5 Report recommendations (`services/report.py`)

The optimization report (`GET /api/v1/report`) applies fixed thresholds
(`report.py:24-46`): `CACHE_HIT_WARN = 0.7` (comment: "healthy Claude Code sits
~0.85-0.95"), `DRIFT_MARGIN = 0.15`, `OUTPUT_PER_TURN_WARN = 2000` /
`OUTPUT_PER_TURN_TARGET = 1000`, `SIDECHAIN_MIN = 0.02`, `UNATTRIBUTED_WARN =
0.15`, `MODEL_CONCENTRATION_WARN = 0.6`, `MIN_DIMENSION_TOKENS = 1_000_000`.

Recommendation **text is Claude-Code-shaped and not provider-gated** (applies
regardless of `event.provider`): the cache rule names `CLAUDE.md`; `model_routing`
fires only when `"opus" in model.lower()` and advises routing to `Haiku`;
`config_drift` names `CLAUDE.md` and `MCP`; the subagent rule uses
"sidechain" terminology. -> TOK-8.

### 3.6 Dashboard provider-specific assumptions (`apps/dashboard/src/`)

The dashboard is a Vue 3 + TypeScript SPA. It has **9 router views** (`router.ts`):
`now` (`/`), `trends`, `blocks`, `breakdowns`, `sessions`, `machines`, `report`,
`alerts`, `settings`. None are provider-scoped. Anthropic/Claude assumptions are
baked throughout; each maps to a generalization epic below. These are recorded
as the v1 UI contract to change deliberately - **not [V1-LOCK]** (the UI is
expected to evolve), but the API shapes they depend on are.

**Limit-window taxonomy (Anthropic-specific) -> TOK-12 + TOK-8:**

- `WINDOW_LABELS` in `format.ts:92-97` hardcodes exactly four window kinds:
  `five_hour` = "5-hour block", `seven_day` = "Weekly", `seven_day_opus` =
  "Weekly (Opus)", `seven_day_sonnet` = "Weekly (Sonnet)". Two are Claude
  model-family-scoped. `windowLabel()` falls back to the raw key for unknown
  kinds (`format.ts:100-102`). No `extra_credits` anywhere in the UI (consistent
  with the collector omission, Section 2.6.5).
- `GaugeCard.vue:36-38`: `isWeekly = window_kind.startsWith('seven_day')` - a
  string-prefix convention decides countdown-vs-date rendering.
- `AlertsView.vue:93-98`: a **second, independent** hardcoded `WINDOWS` array
  (same four strings) drives the alert-rule window `<select>`; default draft is
  `window_kind: 'five_hour'` (`AlertsView.vue:104`). Drift risk vs `format.ts`.

**Model-id humanization (Claude/Bedrock-shaped) -> TOK-8:**

- `modelLabel()` (`format.ts:113-132`) strips an `anthropic.` prefix (Bedrock
  `us.anthropic.claude-...`), a `claude-` prefix, a Bedrock `-v\d+:\d+` suffix,
  and an 8-digit `-YYYYMMDD` date suffix, then parses `family-version`
  (`opus-4-8` -> "Opus 4.8") or legacy `version-family` (`3-7-sonnet` -> "Sonnet
  3.7"). Non-Claude ids pass through unchanged (no formatting). Tests assert
  Claude-only fixtures (`format.test.ts:110-133`).

**Provider dimension largely absent -> TOK-7 + TOK-8:**

- The server `/usage` endpoint supports a `provider` filter and the API client
  builds it (`api/client.ts:367`), **but the primary `FilterBar` (used by Trends
  and Breakdowns) exposes only date-range, `machine`, `project`**
  (`FilterBar.vue`); the `UsageFilter` type omits `provider` entirely
  (`filters.ts:6-11`).
- The only provider selector is a **client-side-only** filter on `SessionsView`
  (`SessionsView.vue:64,76-78,198-201`) whose options are derived from already-
  loaded session data, not a canonical provider list; it does not send the
  `provider` query param.

**Pricing/settings Anthropic defaults -> TOK-5 + TOK-8:**

- `SettingsView.vue:78`: new price rows default to `provider: 'anthropic'` and
  the pricing form has **no provider input field** (`SettingsView.vue:272-289`) -
  no UI path to add non-Anthropic pricing.
- Cache-tier fields `cache_write_short_*`/`cache_write_long_*` and the "cache
  write 5m"/"cache write 1h" placeholders (`SettingsView.vue:280-286`,
  `api/types.ts:9`) encode Anthropic's two-tier (5-minute / 1-hour) prompt-cache
  TTL model as first-class schema.

**Claude-specific copy -> TOK-8:**

- `BreakdownsView.vue:211-213`: cache-card help text names "Claude Code" and
  assumes Anthropic prompt-cache billing ("a high cache-read share (often ~95%)
  is normal").
- `filters.ts:60,68`: the `all` date preset starts at `2020-01-01`, justified in
  the comment as "before any Claude Code usage".

## 4. V1 compatibility contract and golden wire fixtures

This section states the v1 wire contract that every later epic (TOK-2 .. TOK-12)
must preserve, and points to its executable form.

### 4.1 The compatibility rule (PRD Section 10, Migration Phases)

**The v1 ingest and query endpoints stay wire-identical until a formal
deprecation policy exists (Migration Phase 7).** Per PRD Section 10, the v2
service path maps incoming v1 events into v2 rows and applies the legacy
keep-max-output rule as a documented compatibility conflict-resolution mode
(FR-IDEMP-012), and v1 events map to v2 with `event_kind = "attempt"` and
compatibility defaults (FR-EVENT-023). Until Phase 7:

- No v1 request field may be removed, renamed, retyped, or made newly required.
- New v2 fields must be optional on the v1 wire models (which stay
  `extra="forbid"`, so a v1 collector's payload is accepted verbatim).
- No v1 response field may be removed, renamed, or retyped; response envelopes
  keep their shape.
- Status codes and error semantics are unchanged.
- The keep-max-output dedupe outcome is unchanged on v1 endpoints.

### 4.2 Executable contract: the golden suite (AC-001)

The prose above is enforced by `apps/server/tests/integration/test_v1_golden.py`
against fixtures in `apps/server/tests/fixtures/v1_golden/`. This suite is the
executable form of Epic TOK-1 AC-001 and **runs unchanged through every later
epic; any diff is a compatibility break**.

- **Ingest fixtures**: `ingest_events.json` (8 events including a three-line
  `req_dup` keep-max group and case/worktree project variants that fold to one
  group), `ingest_limits.json` (all four Anthropic windows), and
  `ingest_bootstrap.json` (two daily aggregates). Fixed timestamps keep
  date-based grouping deterministic.
- **Byte-stable snapshots** (`responses/*.json`) for the data-driven endpoints:
  `usage` grouped by `day`/`provider`/`model`/`machine`/`project`/`session`,
  `sessions` list, session detail, `limits/current`, `summary/overview`, `cost`.
  Responses are normalized before comparison: ISO-8601 datetimes are masked to
  `"<ts>"`, clock-derived numerics (`age_seconds`) to `"<volatile>"`, and
  unordered object-lists are canonicalized. Value/membership changes are still
  caught; only pure reordering of an unordered collection is tolerated.
- **Structural invariants** for the inherently now-relative endpoints
  (`summary/now`, `limits/history`, `blocks`): required keys and types are
  asserted rather than exact values, so the checks stay valid as the fixed-date
  fixtures age.
- **Regeneration** after an *intended* contract change: run with `WRITE_GOLDEN=1`
  and review the diff by hand before committing.

Verified: the suite passes deterministically on repeated runs, and a deliberate
one-field mutation of a golden fails loudly (proven during 60.4, then reverted).

### 4.3 Dedupe outcome semantics (locked)

The `seeded_client` fixture asserts the exact ingest response counts, locking the
`IngestResult` contract:

- Events: the 8-event batch with a 3-line `req_dup` group returns
  `{"accepted": 6, "duplicates_merged": 2}` - in-batch collapse keeps the
  max-output row (`output_tokens = 648`, not the last or the sum). See Section
  1.3.
- Limits: `{"accepted": 4, "duplicates_merged": 0}` (append-only, no dedupe).
- Bootstrap: `{"accepted": 2, "duplicates_merged": 0}`.

### 4.4 Consolidated [V1-LOCK] inventory

The load-bearing behaviors tagged **[V1-LOCK]** throughout Sections 1-3, gathered
for reference. Each is either asserted by the golden suite or by an existing
integration test.

| # | Locked behavior | Section |
| --- | --- | --- |
| 1 | Composite `usage_events` PK `(provider, event_id)` as the idempotency key | 1.2 |
| 2 | Three-layer keep-max-output dedupe; later lower-output snapshot never wins | 1.3 |
| 3 | `event_id = requestId or message.id` precedence | 1.3 |
| 4 | `daily_rollups` unique grain + `''` sentinels for absent machine/model/project | 1.2, 3.4 |
| 5 | `pricing` unique grain `(provider, model, effective_date)` | 1.2 |
| 6 | `provenance` is a free `String(30)` (incl. `"derived"`), not enum-constrained | 1.4 |
| 7 | Constraint/index naming convention (migration-stable) | 1.1 |
| 8 | `sessions` table present but unpopulated by ingest | 1.2 |
| 9 | Ingest batch caps 5000 / 1000 / 20000; wire models `extra="forbid"` | 2.1 |
| 10 | Validation caps: 10e9 token, 2h clock skew, 1000 utilization; whole-batch reject | 2.2 |
| 11 | `IngestResult` shape `{accepted, duplicates_merged}` | 2.1, 4.3 |
| 12 | Error contract: schema/type -> 422, sanity failure -> 400, auth -> 401 | 2.1, 2.3 |
| 13 | Bearer auth on all routes; bootstrap-token path; WS `?token=` (close 1008) | 2.3 |
| 14 | 16 authenticated query endpoints with their filter dimensions | 2.4 |
| 15 | WebSocket broadcast message shapes; best-effort delivery | 2.5 |
| 16 | Collector wire format mirrors the server ingest schemas exactly | 2.6.7 |
| 17 | Collector offset/dedupe at-least-once safety model | 2.6.2 |
| 18 | Cost = 5-term dot product / 1e6, quantized to micro-USD | 3.2 |
| 19 | Price date-suffix fallback + latest-not-after resolution | 3.1 |
| 20 | Unknown model/provider -> NULL cost (never guessed/zero) -> unpriced alert | 3.2 |
| 21 | Rollups replace-not-accumulate; whole-day recompute | 3.4 |

## 5. Migration constraints and test-to-epic mapping

_Pending - subtask 60.5._
