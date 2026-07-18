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
| 2. Ingest, query, and collector behavior | 60.2 | Pending |
| 3. Pricing, cost, rollups, and dashboard assumptions | 60.3 | Pending |
| 4. V1 compatibility contract and golden wire fixtures | 60.4 | Pending |
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

_Pending - subtask 60.2._

## 3. Pricing, cost, rollups, and dashboard assumptions

_Pending - subtask 60.3._

## 4. V1 compatibility contract and golden wire fixtures

_Pending - subtask 60.4. Will consolidate all [V1-LOCK] items above into an
explicit, testable contract and capture golden wire fixtures + snapshot tests._

## 5. Migration constraints and test-to-epic mapping

_Pending - subtask 60.5._
