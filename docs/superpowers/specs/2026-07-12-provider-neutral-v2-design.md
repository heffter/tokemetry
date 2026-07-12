# Tokemetry Provider-Neutral v2 — Design and Task Plan

- Date: 2026-07-12
- Status: Approved design, pending spec review
- PRD: `.taskmaster/docs/tokemetry_ai_observability_prd.md` (PRD-TOK-002 v1.1)
- Companion: `aiProviderProxy` repo, `aiProviderProxy_multi_protocol_gateway_prd.md` (PRD-AIPP)
- Baseline code: tokemetry master after PR #32 (dashboard-v5, Tasks 1-59 complete)

## 1. Context

Tokemetry today is a Claude Code-first usage tracker: a collector tails Claude Code JSONL transcripts and polls the Anthropic OAuth limits endpoint, a FastAPI server ingests events idempotently (keep-max-output dedupe on `(provider, event_id)`), computes cost at ingest from effective-dated per-MTok prices, refreshes daily rollups, and a Vue dashboard renders limits-first analytics. Provider is already a first-class string on every table and query, but only Anthropic is implemented, the token schema is Anthropic-cache-shaped (five fixed buckets), API tokens have no scopes, and the UI hardcodes Anthropic limit windows.

This program evolves Tokemetry into the provider-neutral system of record described in PRD-TOK-002: attempt-level event lifecycle (snapshots, finality, corrections), provider/model/source registries, rate-card pricing with billable units, dual cost metrics (actual API spend vs subscription-equivalent value), provider-neutral queries/rollups/dashboards, multi-provider limit tracking, aiProviderProxy (RelayPlane) direct export, and a feature-flagged OTLP receiver.

## 2. Resolved decisions

All PRD open questions were resolved with the product owner on 2026-07-12 and are recorded normatively in PRD Section 22 (D-001 through D-018). The decisions that shape this design most:

- D-001: new `usage_events_v2` ledger table; v1 rows backfilled; the physical `usage_events` table is replaced by a read-only v1-shaped compatibility view.
- D-003: dedicated `logical_requests` table from the first release.
- D-006: one generic `rate_cards` table plus `billable_units`; provider specificity lives in pricing strategy plugins.
- D-007: two cost metrics everywhere, keyed by `billing_mode` (`api_billed` vs `subscription`); never merged.
- D-008: limit sources for Anthropic, OpenAI/Codex, and Z.ai all ship in this program (new Epic TOK-12).
- D-009: the OTLP receiver bridge ships in this program, feature-flagged (Epic TOK-11).
- D-011: v2 events must carry source identity; token-to-source binding is optional per token.
- D-012: Python and TypeScript clients generated from OpenAPI plus thin hand-written wrappers.

## 3. Architecture

### 3.1 Data model

New tables (all with Alembic migrations, SQLite and Postgres):

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `providers` | canonical provider registry | `id` (lowercase stable), display_name, aliases (JSON), pricing_strategy, limit_semantics, supported_dimensions, registered (bool) |
| `models` | native model registry | PK (provider, native_model_id); lifecycle (active/deprecated/retired/unknown), capabilities (JSON), first_seen, last_seen |
| `model_aliases` | alias to native mapping | (provider, alias) unique -> native_model_id, rule_version |
| `sources` | reporting source identity | id; type (collector/gateway/sdk/importer/manual), name, version, instance_id, machine (nullable FK), token_label, first_seen, last_seen, billing_mode |
| `usage_events_v2` | active attempt-event state | PK (provider, event_id); event_kind, finality, sequence, logical_request_id, attempt_id, provider_request_id, provider_response_id, requested_model, routed_model, native_model, ts_started/first_token/completed, machine, project, session_id, agent_id, environment, six token counters (input, output, cache_read, cache_write_short, cache_write_long, reasoning), success, outcome, http_status, stop_reason, service_tier, streaming, latency_ms, ttft_ms, tool_call_count, tool_histogram (JSON, opt-in), provenance, source_id (FK), routing (JSON), dimensions (JSON), extra (JSON namespaced), trace_id, span_id, parent_span_id, schema_version; indexes on ts_started, machine, session_id, native_model, logical_request_id, source_id, provider_request_id |
| `usage_event_revisions` | superseded/conflicting states, corrections | FK (provider, event_id), sequence, finality, payload snapshot, reason, actor, ts |
| `logical_requests` | non-billable grouping | PK (provider, logical_request_id); requested_model, session_id, routing policy/reason, attempt_count, fallback_count, winning_attempt_id, first/last ts |
| `billable_units` | non-token unit counts per event | FK (provider, event_id), unit_type, quantity; unique (provider, event_id, unit_type) |
| `rate_cards` | generic pricing | provider, native_model, unit_type, effective_from, effective_to (nullable), currency, region, service_tier, mode (realtime/batch), context_bracket, unit_price (Numeric), source, verified_at, priority, override (bool); overlap rejection enforced in service layer |
| `computed_costs` | cost separated from usage | FK (provider, event_id), pricing_version, cost_status (priced/unpriced/partial/estimated/error), amount, currency, billing_mode, subscription_equivalent_amount, missing_units (JSON), observed_cost, calculated_at; one active row per (event, pricing_version) |
| `ingest_batches` | operational traceability | batch_id, source_id, token_label, counts (accepted/updated/duplicate/rejected/corrected), schema_version, received_at, request_id |
| `data_quality_events` | unknown provider/model, drift, conflicts, unpriced | kind, subject, detail (JSON), source_id, ts; feeds alerts and the data quality page |
| `audit_log` | administrative actions | actor, action, subject, detail (JSON), ts |

Evolved tables: `api_tokens` (+scopes JSON, +source allowlist), `limit_snapshots` (+account, organization, source_id, limit_amount, remaining, unit), `daily_rollups` (new grain: day, provider, native_model, machine, project, source, environment, billing_mode, provenance; adds reasoning/cache columns and cost split by cost_status).

`usage_events` becomes a read-only view projecting `usage_events_v2` to the v1 column shape after backfill verification. Sessions remain query-derived with scoped identity `(provider, source, session_id)`.

### 3.2 Event lifecycle and idempotency

- Identity: `event_id` unique within canonical provider namespace; attempts and logical summaries use distinct IDs.
- Revision resolution: higher `sequence` supersedes lower for snapshots; `final` supersedes any snapshot; final-to-final requires an explicit correction (admin scope) recorded in `usage_event_revisions` and `audit_log`; same-sequence conflicts are rejected and recorded in `data_quality_events`.
- Replay of identical events is a no-op. Batches are transactional. Responses report accepted/updated/duplicate/rejected/corrected counts plus a server batch ID.
- V1 compatibility: v1 ingest maps events into `usage_events_v2` (`event_kind = attempt`, `finality = final`) and applies legacy keep-max-output as a documented compatibility conflict mode, keeping v1 wire behavior bit-identical (verified by golden fixtures).

### 3.3 Ingest and query APIs

- `/api/v2/ingest/events|limits|aggregates|validate`, `/api/v2/schemas/usage-event`, plus query resources `/api/v2/usage|costs|requests|attempts|sessions|providers|models|sources|limits|data-quality|pricing|rollups`.
- Bearer tokens with scopes: `ingest:events`, `ingest:limits`, `ingest:aggregates`, `query:read`, `admin:tokens`, `admin:corrections` (plus implicit full-admin bootstrap token). Ingest-only tokens cannot query. Optional per-token source allowlist.
- Validation errors carry event index, field path, code, message. The validate endpoint runs schema plus semantic checks without persistence. Gzip request bodies supported; ingest and query rate limits are separate; every response carries a request ID.
- Strict privacy: prohibited content-like keys rejected (fuzz-tested), dimensions allowlisted and bounded (D-004), tool-name histogram accepted only behind server-side opt-in (D-005), maximum event size and JSON depth enforced.

### 3.4 Pricing and cost

- `rate_cards` resolution: (provider, native_model, unit_type, timestamp, tier, mode, context_bracket) with priority/override; overlapping conflicting rows rejected; decimal arithmetic only.
- Cost lives in `computed_costs`, never on the usage row. Missing price yields `cost_status = unpriced` (no silent defaults); partial cost names missing units. Cost computation is asynchronous (never blocks or rejects ingest) with an explicit, audited, reversible repricing operation.
- Billing modes (D-007): sources/accounts carry `billing_mode`. `api_billed` usage produces actual spend; `subscription` usage produces subscription-equivalent value at API rates. Rollups, APIs, and dashboards always expose the two as separate metrics.
- Imports: curated official price sources preferred; LiteLLM import supported as labeled fallback; every import (manual or sync) produces a dry-run diff requiring explicit audited apply (D-015). Exporter-observed cost is stored per attempt for reconciliation (D-016); drift is queryable.

### 3.5 Sources, limits, alerts

- Sources auto-register from v2 payload identity (D-011) and are health-tracked (last ingest, error counts, schema version, clock skew). V1 collector traffic is attributed to a derived source so machine tracking stays compatible.
- A provider window registry describes window kinds, labels, and period semantics (FR-LIMIT-012); the dashboard and alerts stop hardcoding `five_hour` etc. Limit sources: existing Anthropic OAuth plus new OpenAI/Codex and Z.ai coding-plan pollers in the collector (D-008), all degrading gracefully to data-quality events on endpoint drift. Gateways may push observed rate-limit headers as snapshots.
- New alert kinds: stale source, unpriced events, unknown model, failure rate, latency, fallback rate, exporter schema drift — all filterable by provider/model/source/project/environment, all content-free. Existing channels (ntfy, Telegram, SMTP), cooldown, and quiet hours are unchanged.

### 3.6 Dashboard

Global provider/model filters; generalized overview/usage/cost views distinguishing cache, reasoning, input, output; dual cost metrics; request/attempt drilldown with fallback-chain visualization; latency and TTFT charts; sources and data-quality pages; pricing administration (rate cards, dry-run diff apply); limits pages driven by the window registry with the existing Claude block/prediction views preserved. No routes removed (D-017).

### 3.7 OpenTelemetry

Trace/span/parent IDs in the v2 schema; a mapping document pinned to a specific GenAI semantic-convention version (D-013); a feature-flagged OTLP receiver converting spans to v2 attempt events with content attributes stripped (D-009). Initial v2 ingest does not depend on OTLP.

### 3.8 Clients

`packages/clients/python` and `packages/clients/typescript`, generated from the server OpenAPI schema in CI with thin hand-written wrappers (auth, batching, retry/backoff). The TypeScript client is the reference implementation for the aiProviderProxy exporter.

## 4. Migration and compatibility plan

Phases follow PRD Section 20. Hard rules: existing collectors keep working unmodified on v1 endpoints for the whole program (AC-001); golden v1 wire fixtures captured in TOK-1 must pass after every epic; every database-changing task ships SQLite and Postgres upgrade/downgrade tests; the v1-to-v2 backfill is verified by count/sum comparison before the compatibility view replaces the physical table; historical costs never change when new prices are added (verified by regression test); retention features are enabled only after rollup verification and backup tests (TOK-10). Maintenance-window upgrades are acceptable for this single-instance self-hosted deployment; rolling upgrades are not a requirement.

## 5. Epic and task breakdown

Twelve parent tasks in the Task Master `master` tag, IDs 60-71. Every subtask is one full quality-gated workflow unit (implement, document, unit + integration tests, ruff, mypy strict, trivy, review, commit) and carries verbose implementation details and a test strategy in Task Master. Requirement IDs from the PRD are referenced in each task.

| ID | Epic | Deps | Subtasks |
|----|------|------|----------|
| 60 | TOK-1 Provider-neutral baseline | — | 5 |
| 61 | TOK-2 Provider and model registries | 60 | 7 |
| 62 | TOK-3 Usage Event v2 ledger + Ingest API v2 | 60, 61 | 12 |
| 63 | TOK-4 Source registry and scoped tokens | 62 | 7 |
| 64 | TOK-5 Pricing and cost engine v2 | 61, 62 | 11 |
| 65 | TOK-6 aiProviderProxy integration | 62, 63, 64 | 7 |
| 66 | TOK-7 Query API and rollups v2 | 62, 64 | 9 |
| 67 | TOK-8 Dashboard generalization | 63, 66 | 9 |
| 68 | TOK-9 Alerts and data quality | 63, 64, 66 | 6 |
| 69 | TOK-12 Multi-provider limit sources | 62, 63 | 7 |
| 70 | TOK-10 Retention, security, operations | 62-69 | 9 |
| 71 | TOK-11 OpenTelemetry interop + OTLP bridge | 62, 66 | 6 |

The usable vertical slice completes with Task 65. Total: 12 parents, 95 subtasks.

### Task 60 — TOK-1 Provider-Neutral Baseline (5 subtasks)

1. Audit data model and dedupe semantics: document every table, constraint, index, the `(provider, event_id)` keep-max dedupe, rollup grain, and pricing grain in `docs/architecture/provider-neutral-baseline.md`.
2. Audit ingest/query APIs and collector: v1 endpoints, batch/validation limits, collector offsets/queue, OAuth limits source, WebSocket behavior.
3. Audit pricing, cost, rollups, and dashboard assumptions: CostEngine flow, LiteLLM sync, all Anthropic-specific UI code paths (window labels, model label parsing, report recommendations).
4. Capture the V1 compatibility contract: golden wire fixtures for events/limits/bootstrap ingest and key query responses; snapshot tests that lock current behavior.
5. Document migration constraints and map existing tests to epics: SQLite/Postgres duality, Alembic policy, backup strategy; commit the baseline document (unblocks all other epics).

### Task 61 — TOK-2 Provider and Model Registries (7 subtasks)

1. Core provider descriptors and alias normalization (`z.ai`/`zai` etc.) in `packages/core`.
2. `providers`, `models`, `model_aliases` tables and migration with upgrade/downgrade tests.
3. Registry services with seeds for anthropic/openai/zai and unknown-provider/model policy (mark unregistered, ingest per policy).
4. `data_quality_events` table and recording service.
5. `GET /api/v2/providers` and `GET /api/v2/models` (native + normalized fields, lifecycle, aliases).
6. Backfill registries from historical usage data; produce unknown-model data-quality records.
7. Tests, fixtures for all three providers, and `docs/architecture/registries.md`; regression test that existing Claude data stays queryable.

### Task 62 — TOK-3 Usage Event v2 Ledger and Ingest API v2 (12 subtasks)

1. Core v2 wire model (`usage_v2.py`) with full field set and published JSON schema.
2. Privacy validation layer: prohibited keys, dimension allowlist/bounds, tool-histogram gate (default off), size/depth limits, fuzz tests.
3. Migration for `usage_events_v2`, `usage_event_revisions`, `logical_requests` with indexes.
4. Revision engine: sequence/finality resolution, conflict rejection to data quality, correction semantics with audit.
5. Ingest service v2: transactional batches, result counts, `ingest_batches`, request IDs.
6. `POST /api/v2/ingest/events` and `POST /api/v2/ingest/validate` with structured errors, gzip, limits.
7. `POST /api/v2/ingest/limits` and `POST /api/v2/ingest/aggregates`.
8. V1-to-v2 backfill migration plus verification tooling (count/sum equality per day/provider/machine).
9. Repoint v1 ingest and all services to the v2 ledger with keep-max compatibility mode; golden v1 fixtures must pass.
10. Replace `usage_events` with the v1 compatibility view; Grafana continuity check.
11. `logical_requests` population and winning-attempt identification.
12. OpenAPI v2 publication, `GET /api/v2/schemas/usage-event`, ingest docs, performance tests (100-event p95, 5000-event compatibility batch).

### Task 63 — TOK-4 Source Registry and Scoped Tokens (7 subtasks)

1. `sources` table and auto-registration from v2 payloads.
2. Source health service: last ingest, error counts, schema version, clock skew.
3. Token scopes migration and scope model (including `admin:corrections`); bootstrap token stays full-admin.
4. Scope enforcement across v1/v2 routers and WebSocket; ingest-only cannot query; auth failures reveal nothing.
5. `GET /api/v2/sources` and token administration v2 (scoped create, rotate, revoke, last-used).
6. V1 source attribution: derive a source identity for legacy collector traffic so machine tracking stays compatible.
7. Tests and `docs/architecture/source-health.md`.

### Task 64 — TOK-5 Pricing and Cost Engine v2 (11 subtasks)

1. `rate_cards` table and lossless migration of existing pricing rows; overlap rejection.
2. `billable_units` table and unit-type vocabulary.
3. Rate resolution service (tier, mode, context bracket, priority, override).
4. `computed_costs` table with cost_status, pricing version, observed_cost.
5. Cost engine v2: token units plus billable units; unpriced/partial semantics.
6. Asynchronous cost worker and audited, reversible repricing job; ingest isolation.
7. Provider pricing strategy plugins: anthropic (ported), openai (cached input, reasoning), zai (cached input), generic.
8. Billing modes and dual metrics (actual spend vs subscription-equivalent value).
9. LiteLLM import v2 into rate cards with dry-run diff and explicit audited apply.
10. Pricing admin API v2: rate-card CRUD, imports, unpriced/unknown reports.
11. Historical-stability and migration-equality regression tests; `docs/architecture/pricing-v2.md`.

### Task 65 — TOK-6 aiProviderProxy Integration (7 subtasks)

1. Integration contract document: CanonicalUsageEvent-to-v2 mapping, identity, finality, error vocabulary, privacy, version negotiation (`docs/integrations/ai-provider-proxy.md`).
2. TypeScript client package generated from OpenAPI plus wrapper (auth, batching, retry, backoff).
3. Python client package, same pattern.
4. Integration test harness: test server plus mock proxy exporter fixtures for all three providers including retries, fallbacks, snapshots.
5. End-to-end tests: three providers' events land correctly; fallbacks never double count; requested vs routed model visible.
6. Ingest-only token provisioning runbook, source freshness verification, observed-cost drift query.
7. Contract conformance suite: golden payloads shared with the proxy repo; content-free payload tests; poison-event (400/422) behavior verification.

### Task 66 — TOK-7 Query API and Rollups v2 (9 subtasks)

1. `daily_rollups` v2 grain migration (source, environment, billing_mode, reasoning/cache, cost by status) and data migration.
2. Rollup service v2: final attempts only, incremental, idempotent, correction-triggered recomputation.
3. Query framework: keyset pagination, explicit sort, selectable grain, bounded raw ranges, data-quality warnings.
4. `/api/v2/usage` and `/api/v2/costs` (dual metrics, cost status, pricing version).
5. `/api/v2/requests`, `/api/v2/attempts`, `/api/v2/sessions` (scoped identity, winning attempt, no double counting).
6. `/api/v2/limits`, `/api/v2/data-quality`, `/api/v2/pricing`, `/api/v2/rollups` query endpoints (`/api/v2/sources` ships in Task 63).
7. CSV export and Grafana-compatible v2 views.
8. Performance benchmarks (30-day p95, high-cardinality dimensions).
9. V1 query parity regression suite over the v2 ledger.

### Task 67 — TOK-8 Dashboard Generalization (9 subtasks)

1. Global provider/model filter store and v2 typed API client layer.
2. Overview generalization with official-vs-estimated labels and zero/unavailable/unsupported distinction.
3. Usage views: cache/reasoning/input/output composition, registry-driven model labels.
4. Cost views: dual metrics, cost status, reconciliation drift panel.
5. Requests and attempts pages: logical-request drilldown, fallback-chain visualization, latency and TTFT charts, failure rates.
6. Sessions and agents generalization with scoped identity.
7. Sources and machines page with health, freshness, schema drift.
8. Data quality page and pricing administration page (rate cards, diff review, apply).
9. Limits pages driven by the window registry; preserve Claude block/prediction views; navigation restructure.

### Task 68 — TOK-9 Alerts and Data Quality (6 subtasks)

1. Alert rule filters: provider, model, source, project, environment.
2. Stale-source alert kind built on source health.
3. Unpriced-event and unknown-model alert kinds on v2 data.
4. Failure-rate, latency, and fallback-rate alert kinds.
5. Exporter schema-drift alert kind.
6. Content-free context verification, channel regression tests, docs.

### Task 69 — TOK-12 Multi-Provider Limit Sources (7 subtasks)

1. Provider window registry (kinds, labels, periods) exposed through the providers API.
2. `limit_snapshots` migration: account, organization, source dimensions plus amounts and units; multi-account grouping rule.
3. OpenAI/Codex limits source in the collector with graceful degradation.
4. Z.ai coding-plan limits source in the collector with graceful degradation.
5. Gateway-observed rate-limit header snapshots through v2 limits ingest.
6. Forecasting with source-data and confidence labeling; no cross-account merging.
7. Tests and documentation.

### Task 70 — TOK-10 Retention, Security, Operations (9 subtasks)

1. Retention configuration model with per-category durations and legal hold.
2. Incremental, resumable retention jobs; superseded-snapshot compaction after 7 days; rollup verification before deletion.
3. Administrative deletion APIs (source, machine, project, time range).
4. `audit_log` table wired to every administrative action.
5. Rate limiting split (ingest vs query), request size and JSON depth limits, CORS/TLS hardening guidance.
6. Backup and restore automation with restore tests on SQLite and Postgres.
7. Runbooks: migration and rollback, token rotation, upgrade; operational retention status.
8. Security and privacy test suite: prohibited-key fuzzing, scope bypass, revoked tokens, WebSocket auth, secret redaction, SQL/filter injection.
9. Performance and security gates: sustained-load test, trivy, release acceptance checklist (AC-001 through AC-022).

### Task 71 — TOK-11 OpenTelemetry Interoperability (6 subtasks)

1. Trace, span, and parent linkage in the v2 schema including agent parent-child support.
2. Semantic-convention mapping document with pinned version, recorded on mapped events.
3. Feature-flagged OTLP receiver service (off by default, authenticated).
4. Span-to-v2 attempt converter with content-attribute stripping.
5. End-to-end test with an OTel-instrumented sample application.
6. Documentation and deferral note for export-to-OTel-backend.

## 6. Testing strategy

Follows PRD Section 18 in full. Every DB-changing subtask ships migration tests (upgrade and downgrade, SQLite and Postgres). Every ingest or metadata subtask ships privacy validation tests. Golden v1 fixtures from Task 60 run in CI for the entire program. End-to-end suites cover: Claude collector to dashboard; proxy Anthropic/OpenAI/Z.ai events to dashboard; multi-attempt fallback; unpriced model workflow; limit snapshot to alert; OTLP span to dashboard. Performance suites cover 100-event p95, 5000-event compatibility batches, 1000 events/s sustained, high-cardinality dimensions, and rollup recomputation. Quality gates: pytest 100 percent pass, coverage at or above 80 percent line / 70 percent branch, ruff zero warnings, mypy strict clean, trivy no HIGH/CRITICAL.

## 7. Risks

PRD Section 21 applies unchanged. Program-specific additions: the Codex and Z.ai limit endpoints are undocumented and may drift (mitigated by FR-LIMIT-013 graceful degradation and data-quality surfacing); the v1-to-v2 backfill is the highest-risk migration step (mitigated by count/sum verification before the view swap and by keeping a reversible migration); OTLP semantic conventions for generative AI are still evolving (mitigated by pinning and recording the semconv version, D-013).

## 8. Planning deliverables

This planning branch delivers: PRD-TOK-002 updated to v1.1 with the decision log; this design document; 12 parent tasks and 95 subtasks created in Task Master (master tag, manual mode, no AI generation); all committed on a branch cut from master after PR #32 merges, with a PR back to master.
