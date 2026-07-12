# Product Requirements Document: Tokemetry Provider-Neutral AI Usage Observability Platform

**Document ID:** PRD-TOK-002  
**Status:** Approved for implementation (all open questions resolved; see Section 22)  
**Version:** 1.1  
**Date:** 2026-07-12  
**Repository:** `heffter/tokemetry`  
**Primary consumer:** Claude Task Master  
**Companion PRD:** `aiProviderProxy_multi_protocol_gateway_prd.md`

---

## 1. Executive Summary

Tokemetry will evolve from a Claude Code–first token-usage tracker into a provider-neutral, self-hosted AI usage observability and cost intelligence platform.

The product will preserve its current strengths:

- Self-hosted deployment.
- Multi-machine collection.
- Idempotent ingest.
- Durable storage.
- Versioned pricing.
- Limit tracking.
- Historical analytics.
- Privacy-preserving metadata-only operation.
- API-first architecture.

It will add the data model, ingest semantics, pricing flexibility, query dimensions, and dashboards required for modern AI gateways and agents.

The first integration target is aiProviderProxy, which will export authoritative request-attempt usage records for Anthropic, OpenAI, and Z.ai models. The architecture must also support direct SDK instrumentation, local collectors, importers, and future gateway integrations.

Tokemetry owns durable accounting, historical pricing, provider-limit history, usage analytics, cost analytics, alerting, and reporting. It does not execute model requests, route traffic, translate protocols, or store prompt and response content.

---

## 2. Problem Statement

AI usage accounting is no longer a simple pair of input and output token counts.

Modern providers expose:

- Cached input tokens.
- Cache writes with different TTLs.
- Reasoning tokens.
- Service tiers.
- Batch discounts.
- Long-context pricing thresholds.
- Web search and hosted-tool request charges.
- Audio, image, video, and storage units.
- Provider-specific rate-limit windows.
- Multiple retries and fallback attempts for one logical request.
- Streaming snapshots that revise usage over time.
- Provider-native request IDs and gateway-generated trace IDs.
- Subscription limits that differ from API spend.
- Provider aliases and rapidly changing model IDs.

A Claude-specific schema can represent some of these concepts but cannot become a durable, provider-neutral accounting platform without explicit evolution.

The core challenge is to preserve strong normalization while avoiding a lowest-common-denominator schema that discards important billing and operational data.

---

## 3. Product Vision

Tokemetry becomes the self-hosted system of record for AI consumption:

```text
Local Collectors ───────────────┐
AI Gateways, including          │
aiProviderProxy ────────────────┼──> Versioned Ingest API
Direct SDK Exporters ───────────┤          │
Historical Importers ──────────┘          ▼
                                   Normalization and Validation
                                             │
                     ┌───────────────────────┼───────────────────────┐
                     ▼                       ▼                       ▼
               Usage Ledger           Pricing Engine          Limit Ledger
                     │                       │                       │
                     └─────────────── Rollups and Analytics ───────┘
                                             │
                                     REST / WebSocket / UI
```

Tokemetry should answer:

- Which provider and model consumed resources?
- Which machine, project, session, agent, or gateway generated the usage?
- Which logical request and upstream attempt produced the cost?
- How much usage was cached, reasoned, streamed, or tool-related?
- What price schedule was effective at the time?
- What was the authoritative cost?
- How often did routing fall back?
- Which providers are reliable or slow?
- When will subscription or rate limits be exhausted?
- Which data is official and which is estimated?
- Is any source failing to report data?

---

## 4. Goals

### 4.1 Primary Goals

- G-001: Support normalized usage from Anthropic, OpenAI, Z.ai, and future providers.
- G-002: Preserve backward compatibility with current Claude Code collectors and ingest clients.
- G-003: Add a versioned event model capable of representing attempts, revisions, finality, outcomes, latency, reasoning, tools, and routing metadata.
- G-004: Keep token counts strongly typed while supporting additional billable units.
- G-005: Make pricing date-versioned, provider-aware, model-aware, tier-aware, and extensible beyond token pricing.
- G-006: Accept direct gateway exporters such as aiProviderProxy without requiring a local file collector.
- G-007: Maintain idempotent and replay-safe ingestion.
- G-008: Prevent retries, snapshots, and logical summaries from double-counting usage.
- G-009: Provide provider-neutral dashboards and query APIs.
- G-010: Remain metadata-only by default and never require prompt or response storage.
- G-011: Expose data quality and source health as first-class operational signals.
- G-012: Support future OpenTelemetry interoperability.

### 4.2 Secondary Goals

- G-013: Support provider-specific limits and subscription windows.
- G-014: Support gateway routing and fallback analysis.
- G-015: Support cost reconciliation between exporter estimates and Tokemetry pricing.
- G-016: Support enterprise dimensions such as organization, team, environment, and cost center.
- G-017: Support configurable retention and aggregation.
- G-018: Support external dashboards such as Grafana without proprietary database features.
- G-019: Support generic exporters through documented schemas and client libraries.
- G-020: Provide a stable plugin architecture for collectors, pricing sources, and provider semantics.

---

## 5. Non-Goals

- NG-001: Tokemetry will not route or proxy model requests.
- NG-002: Tokemetry will not store prompts, responses, source files, code, or tool arguments by default.
- NG-003: Tokemetry will not become a prompt evaluation or model-quality platform in the first release.
- NG-004: Tokemetry will not calculate provider invoices with legal or financial guarantees.
- NG-005: Tokemetry will not require OpenTelemetry infrastructure for core operation.
- NG-006: Tokemetry will not force every provider into Anthropic cache semantics.
- NG-007: Tokemetry will not infer hidden reasoning content.
- NG-008: Tokemetry will not implement client-side provider authentication.
- NG-009: Tokemetry will not make provider terms or subscription-plan compliance decisions.
- NG-010: Tokemetry will not replace specialized distributed tracing backends.

---

## 6. Personas

### 6.1 Individual Developer

Wants a private, self-hosted history of AI usage across multiple machines and tools.

### 6.2 Engineering Manager

Needs usage, cost, project, model, and agent-level reporting.

### 6.3 Platform Engineer

Operates AI gateways, collectors, exporters, tokens, backups, alerts, and data retention.

### 6.4 FinOps Owner

Needs date-correct pricing, cost allocation, unknown-price visibility, and reconciliation.

### 6.5 Security and Privacy Owner

Needs metadata minimization, access control, retention controls, and auditable ingestion.

### 6.6 Integration Developer

Builds exporters and requires stable schemas, idempotency, client libraries, and compatibility guarantees.

---

## 7. Key User Stories

- US-001: As an aiProviderProxy operator, I can export usage directly to Tokemetry.
- US-002: As a Claude Code user, my existing collector continues to work after the platform upgrade.
- US-003: As a FinOps owner, I can distinguish uncached input, cache reads, cache writes, output, and reasoning usage.
- US-004: As an operator, I can see each retry or fallback attempt without double-counting a logical request.
- US-005: As an operator, I can see the requested model and the actual routed model.
- US-006: As an operator, I can compare provider latency, error rates, and fallback rates.
- US-007: As a privacy owner, I can verify that content is not accepted or stored.
- US-008: As a platform engineer, I can provision a revocable ingest-only token for each gateway.
- US-009: As a maintainer, I can add a new provider pricing strategy without changing dashboard code.
- US-010: As a user, I can identify usage events that have no applicable price row.
- US-011: As an operator, I can see whether an event is a provider-reported fact or a local estimate.
- US-012: As an operator, I can inspect malformed or stale exporters.
- US-013: As an enterprise user, I can allocate spend to teams, environments, and cost centers.
- US-014: As an integration developer, I can use a documented schema and generated client.
- US-015: As an operator, I can configure data retention without losing long-term aggregates.

---

## 8. Product Principles

- PP-001: Preserve provider facts before deriving normalized analytics.
- PP-002: Raw usage metadata is immutable except through explicit event revision semantics.
- PP-003: Historical pricing must not change when current prices change.
- PP-004: Attempts are billable facts; logical requests are analytical groupings.
- PP-005: Idempotency must survive retries, process restarts, and network ambiguity.
- PP-006: Core columns represent common high-value dimensions; provider-specific data uses typed extensions.
- PP-007: Do not overload token fields with non-token units.
- PP-008: Unknown pricing is visible, not silently estimated with a generic default.
- PP-009: Content-free operation is the default and required for standard ingest.
- PP-010: Schema and API evolution are versioned.
- PP-011: Collectors and direct exporters are equal first-class sources.
- PP-012: Operational data quality is part of observability.

---

## 9. Target Product Boundary

### Tokemetry Owns

- Usage event ingestion.
- Event validation.
- Event revision and idempotency.
- Provider and model normalization.
- Durable usage storage.
- Pricing catalogs and effective-date selection.
- Cost computation.
- Limit snapshots and utilization history.
- Rollups.
- Query APIs.
- Dashboards.
- Alerts.
- Retention.
- Data quality monitoring.
- Exporter token management.

### aiProviderProxy Owns

- Client protocol compatibility.
- Upstream provider execution.
- Authentication to model providers.
- Routing and fallback.
- Attempt lifecycle.
- Provider response parsing.
- Immediate extraction of provider usage metadata.
- Durable local export queue.
- Export retry and dead-letter handling.

### Shared Contract

- Event schemas.
- Canonical provider IDs.
- Canonical model IDs and aliases.
- Event identity and finality.
- Error and outcome vocabulary.
- Privacy rules.
- Version negotiation.

---

## 10. Current-State Compatibility Requirement

Before schema changes, the implementation must inventory:

- Current `UsageEvent`, `DailyAggregate`, `LimitSnapshot`, and `PriceRow`.
- Current ingest endpoints and validation limits.
- Current database constraints.
- Current dedupe behavior.
- Current rollup behavior.
- Current collector queue behavior.
- Current provider registry.
- Current pricing strategies and LiteLLM import support.
- Current dashboard assumptions.
- Current API clients and deployment documentation.

The audit output must be committed as:

`docs/architecture/provider-neutral-baseline.md`

All migration tasks must reference this baseline.

---

## 11. Data Model Strategy

Tokemetry will support two API generations during migration:

- Ingest v1: current event schema and behavior.
- Ingest v2: provider-neutral event lifecycle schema.

V1 remains supported for existing collectors. V1 events are normalized into the v2 internal ledger with compatibility defaults.

Decision (D-001, 2026-07-12): the v2 ledger is a new table (`usage_events_v2`) rather than an in-place evolution of `usage_events`. Migration sequence:

1. Create the v2 tables (`usage_events_v2`, `usage_event_revisions`, `logical_requests`, `billable_units`, `computed_costs`).
2. Backfill every v1 row into `usage_events_v2` with `event_kind = "attempt"`, `finality = "final"`, provenance preserved, and the existing `cost_usd` value carried into `computed_costs` under pricing version `v1-legacy`.
3. Verify the backfill by comparing row counts and token and cost sums per day, provider, and machine.
4. Repoint all server services and queries to the v2 ledger.
5. Replace the physical `usage_events` table with a read-only compatibility view that projects `usage_events_v2` back to the v1 column shape, so external readers such as Grafana keep working.

V1 ingest endpoints stay wire-identical. The v1 service path maps incoming events into v2 rows and applies the legacy keep-max-output rule as a documented compatibility conflict-resolution mode (see FR-IDEMP-012).

---

## 12. Functional Requirements

## 12.1 Provider Registry

- FR-PROVIDER-001: Tokemetry MUST maintain a canonical provider registry.
- FR-PROVIDER-002: Provider IDs MUST be lowercase stable identifiers.
- FR-PROVIDER-003: Provider aliases such as `z.ai`, `zai`, and other legacy names MUST normalize centrally.
- FR-PROVIDER-004: The registry MUST retain display name, aliases, pricing strategy, limit semantics, and supported usage dimensions.
- FR-PROVIDER-005: Unknown providers MAY be ingested when policy permits but MUST be marked unregistered.
- FR-PROVIDER-006: Provider-specific extensions MUST use a provider namespace.
- FR-PROVIDER-007: Provider registration MUST not require dashboard code changes.
- FR-PROVIDER-008: Initial registered providers MUST include Anthropic, OpenAI, and Z.ai.
- FR-PROVIDER-009: Existing Claude Code provider IDs MUST remain compatible.
- FR-PROVIDER-010: Registry metadata MUST be queryable through the API.

## 12.2 Model Registry

- FR-MODEL-001: Tokemetry MUST retain the exact native model ID reported by the exporter.
- FR-MODEL-002: Model aliases MUST be stored separately from native IDs.
- FR-MODEL-003: Pricing lookup MUST use provider plus native model ID plus effective date and applicable dimensions.
- FR-MODEL-004: The registry MUST support lifecycle states such as active, deprecated, retired, and unknown.
- FR-MODEL-005: The registry SHOULD retain capability metadata for display and validation.
- FR-MODEL-006: Unknown models MUST be visible in data quality reports.
- FR-MODEL-007: Model metadata updates MUST not rewrite historical events.
- FR-MODEL-008: Initial support MUST include relevant Claude, OpenAI/Codex, and GLM model families.
- FR-MODEL-009: Model alias rules MUST be versioned.
- FR-MODEL-010: API responses MUST provide native and normalized model fields where both exist.

## 12.3 Usage Event v2

Recommended wire model:

```json
{
  "schema_version": 2,
  "event_id": "provider:req_123",
  "event_kind": "attempt",
  "finality": "final",
  "sequence": 1,

  "logical_request_id": "lr_123",
  "attempt_id": "att_456",
  "provider_request_id": "req_123",
  "provider_response_id": "msg_123",

  "provider": "anthropic",
  "native_model": "claude-sonnet-...",
  "requested_model": "relayplane:auto",
  "routed_model": "claude-sonnet-...",

  "ts_started": "2026-07-10T12:00:00Z",
  "ts_first_token": "2026-07-10T12:00:01Z",
  "ts_completed": "2026-07-10T12:00:03Z",

  "machine": "devbox-01",
  "project": "project-alias",
  "session_id": "session-123",
  "agent_id": "agent-123",
  "environment": "development",

  "input_tokens": 1000,
  "output_tokens": 300,
  "cache_read_tokens": 800,
  "cache_write_short_tokens": 0,
  "cache_write_long_tokens": 0,
  "reasoning_tokens": 120,

  "success": true,
  "outcome": "success",
  "http_status": 200,
  "stop_reason": "end_turn",
  "service_tier": "standard",
  "streaming": true,
  "latency_ms": 3000,
  "time_to_first_token_ms": 1000,
  "tool_call_count": 2,

  "provenance": "official",
  "source": {
    "type": "gateway",
    "name": "aiProviderProxy",
    "version": "x.y.z",
    "instance_id": "proxy-01"
  },

  "routing": {
    "policy": "cascade",
    "reason": "complexity",
    "attempt_index": 0,
    "fallback_from": null,
    "fallback_trigger": null
  },

  "dimensions": {
    "team": "platform",
    "cost_center": "RND"
  },

  "extra": {
    "anthropic": {},
    "gateway": {}
  }
}
```

Requirements:

- FR-EVENT-001: `schema_version` MUST be required for v2.
- FR-EVENT-002: `event_id` MUST be unique within the canonical provider namespace.
- FR-EVENT-003: `event_kind` MUST distinguish attempt, logical_request, import, adjustment, and limit-related records where applicable.
- FR-EVENT-004: Billable usage MUST be associated with attempt events, not logical request summaries.
- FR-EVENT-005: `finality` MUST distinguish snapshot and final.
- FR-EVENT-006: `sequence` MUST increase for revised snapshots.
- FR-EVENT-007: Final events MUST supersede earlier snapshots with the same event ID.
- FR-EVENT-008: A later snapshot MUST not supersede a final event unless explicit correction semantics are used.
- FR-EVENT-009: `logical_request_id` MUST group retries and fallback attempts.
- FR-EVENT-010: `attempt_id` MUST identify one upstream attempt.
- FR-EVENT-011: Provider request and response IDs MUST be optional but indexed when present.
- FR-EVENT-012: Requested, routed, and native model identifiers MUST be separate.
- FR-EVENT-013: Started, first-token, and completed timestamps MUST be timezone-aware.
- FR-EVENT-014: Token counters MUST be non-negative integers.
- FR-EVENT-015: Reasoning tokens MUST be stored separately from visible output tokens.
- FR-EVENT-016: Unknown usage counters MUST be retained in typed provider extension metadata.
- FR-EVENT-017: Success and outcome MUST be separate to allow nuanced terminal states.
- FR-EVENT-018: Source identity MUST include source type, name, version, and optional instance ID.
- FR-EVENT-019: Routing metadata MUST be optional and gateway-neutral.
- FR-EVENT-020: Arbitrary dimensions MUST be bounded by key count, key length, and value length.
- FR-EVENT-021: Content fields MUST not exist in the standard usage schema.
- FR-EVENT-022: Events containing prohibited content-like keys MUST be rejected or stripped according to strict server policy.
- FR-EVENT-023: V1 events MUST map into v2 with `event_kind = "attempt"` and compatibility defaults.
- FR-EVENT-024: Failed and cancelled attempts MUST be ingestible even when token counters are zero.
- FR-EVENT-025: Events MUST retain provenance: official, local_estimate, stats_cache, imported, or adjusted.
- FR-EVENT-026: Event correction MUST be explicit and auditable.
- FR-EVENT-027: Original source payload MAY be retained only as content-free structured metadata under configured limits.
- FR-EVENT-028: Maximum event size MUST be enforced.

## 12.4 Idempotency, Snapshots, and Corrections

Current keep-maximum-output behavior is useful for streamed snapshots but insufficient as a complete v2 revision model.

- FR-IDEMP-001: Ingest MUST be idempotent by event ID.
- FR-IDEMP-002: Snapshot replacement MUST compare sequence and finality.
- FR-IDEMP-003: Higher sequence snapshots supersede lower sequence snapshots.
- FR-IDEMP-004: Final supersedes snapshot.
- FR-IDEMP-005: Final-to-final changes require a correction record or explicit correction flag.
- FR-IDEMP-006: Corrections MUST retain who or what made the correction, timestamp, reason, and previous values.
- FR-IDEMP-007: Replayed identical events MUST be no-ops.
- FR-IDEMP-008: Conflicting events with the same sequence MUST be rejected and surfaced as data quality failures.
- FR-IDEMP-009: Batch ingest MUST remain transactional.
- FR-IDEMP-010: Invalid events SHOULD be reportable individually through a validation-only endpoint before batch submission.
- FR-IDEMP-011: Ingest responses SHOULD report accepted, updated, duplicate, rejected, and corrected counts.
- FR-IDEMP-012: Historical v1 keep-max behavior MUST remain unchanged on v1 endpoints.

## 12.5 Ingest API v2

Recommended endpoints:

```text
POST /api/v2/ingest/events
POST /api/v2/ingest/limits
POST /api/v2/ingest/aggregates
POST /api/v2/ingest/validate
GET  /api/v2/schemas/usage-event
GET  /api/v2/providers
GET  /api/v2/models
```

Requirements:

- FR-INGEST-001: V2 ingest MUST use bearer authentication.
- FR-INGEST-002: Tokens MUST support scopes.
- FR-INGEST-003: Initial scopes MUST include `ingest:events`, `ingest:limits`, `ingest:aggregates`, `query:read`, and `admin:tokens`.
- FR-INGEST-004: Ingest-only tokens MUST not access query endpoints.
- FR-INGEST-005: Batch maximum count and byte size MUST be configurable.
- FR-INGEST-006: Validation errors MUST include event index, field path, code, and message.
- FR-INGEST-007: The validation endpoint MUST perform schema and semantic checks without persistence.
- FR-INGEST-008: Ingest MUST return a server-generated batch ID.
- FR-INGEST-009: Ingest SHOULD return accepted and updated event IDs when requested, subject to response-size limits.
- FR-INGEST-010: Request compression SHOULD support gzip.
- FR-INGEST-011: API version negotiation MUST be documented.
- FR-INGEST-012: OpenAPI MUST describe all v2 schemas.
- FR-INGEST-013: Client libraries SHOULD be generated for Python and TypeScript.
- FR-INGEST-014: V1 endpoints MUST remain available through the migration window.
- FR-INGEST-015: Rate limits MUST distinguish ingest and query traffic.
- FR-INGEST-016: Server responses MUST include a request ID.
- FR-INGEST-017: Ingest logs MUST not include bearer tokens or event metadata values classified as sensitive.
- FR-INGEST-018: Health checks MUST not require authentication.
- FR-INGEST-019: Readiness MUST report database and migration status without exposing secrets.
- FR-INGEST-020: Optional source allowlists SHOULD be supported per token.

## 12.6 Source Registry and Health

- FR-SOURCE-001: Tokemetry MUST register reporting sources.
- FR-SOURCE-002: Sources MUST include collector, gateway, SDK, importer, and manual adjustment types.
- FR-SOURCE-003: Source identity MUST not be conflated with machine identity.
- FR-SOURCE-004: Source records MUST retain first seen, last seen, version, instance ID, machine, and token label.
- FR-SOURCE-005: Source health MUST include last successful ingest, recent error count, schema version, and clock skew.
- FR-SOURCE-006: Dashboard MUST identify stale sources.
- FR-SOURCE-007: Alerts SHOULD support stale-source conditions.
- FR-SOURCE-008: The existing machine table MUST remain supported.
- FR-SOURCE-009: One machine MAY host multiple sources.
- FR-SOURCE-010: Source labels MUST be mutable without changing event identity.
- FR-SOURCE-011: API tokens SHOULD be attributable to a source or source group.
- FR-SOURCE-012: Revoked sources MUST not delete historical data.

## 12.7 Pricing Architecture

Token-only `PriceRow` remains supported but evolves into a flexible rate-card system.

### 12.7.1 Pricing Concepts

- Provider.
- Native model.
- Effective start date and optional end date.
- Currency.
- Region.
- Service tier.
- Batch or realtime mode.
- Context-length bracket.
- Unit type.
- Unit price.
- Source and verification timestamp.
- Priority and override status.

### 12.7.2 Billable Unit Types

Initial unit types:

- `input_token`
- `output_token`
- `cache_read_token`
- `cache_write_short_token`
- `cache_write_long_token`
- `reasoning_token` when separately priced
- `request`
- `web_search_request`
- `tool_call`
- `image_input`
- `image_output`
- `audio_input_second`
- `audio_output_second`
- `video_second`
- `storage_byte_hour`
- `batch_input_token`
- `batch_output_token`

Requirements:

- FR-PRICE-001: Historical cost MUST use the rate effective at event time.
- FR-PRICE-002: Missing pricing MUST produce `cost_status = "unpriced"`, not a generic default.
- FR-PRICE-003: Price source and verification date MUST be stored.
- FR-PRICE-004: Manual overrides MUST take precedence according to documented priority.
- FR-PRICE-005: Overlapping conflicting rates MUST be rejected.
- FR-PRICE-006: Rate cards MUST support service-tier and batch distinctions.
- FR-PRICE-007: Rate cards SHOULD support context-threshold pricing.
- FR-PRICE-008: Rate cards MUST support provider-specific unit combinations.
- FR-PRICE-009: Existing `PriceRow` data MUST migrate without changing historical results.
- FR-PRICE-010: Anthropic cache short and long write prices MUST remain supported.
- FR-PRICE-011: OpenAI cached input and reasoning counters MUST be supported.
- FR-PRICE-012: Z.ai cached input pricing MUST be supported.
- FR-PRICE-013: Tool or search request fees MUST be additive to token fees.
- FR-PRICE-014: Price imports MUST support dry run and diff.
- FR-PRICE-015: Price changes MUST be auditable.
- FR-PRICE-016: Automatic price updates MUST never rewrite past effective periods silently.
- FR-PRICE-017: Cost computation MUST use decimal arithmetic.
- FR-PRICE-018: Cost calculation version MUST be retained on events or cost records.
- FR-PRICE-019: Repricing MUST be an explicit administrative operation.
- FR-PRICE-020: Repricing results MUST be auditable and reversible.
- FR-PRICE-021: Official provider sources are preferred; LiteLLM may be used as a machine-readable fallback with source labeling.
- FR-PRICE-022: The system MUST expose unknown model and unpriced event reports.

## 12.8 Cost Records

Cost should be separated from immutable raw usage where practical.

- FR-COST-001: Each final attempt event MUST have zero or one active authoritative computed cost record per pricing version.
- FR-COST-002: Cost records MUST retain currency, amount, pricing source, pricing version, and calculation timestamp.
- FR-COST-003: Exporter-observed cost MAY be retained as a reconciliation value.
- FR-COST-004: Observed cost MUST not replace authoritative cost automatically.
- FR-COST-005: Cost drift MUST be queryable.
- FR-COST-006: Cost status MUST include priced, unpriced, partial, estimated, and error.
- FR-COST-007: Partial cost MUST identify missing billable units.
- FR-COST-008: Cost computation failures MUST not reject otherwise valid usage ingest.
- FR-COST-009: Cost computation SHOULD run asynchronously when ingest latency would otherwise be materially affected.
- FR-COST-010: Rollups MUST distinguish authoritative and estimated cost.
- FR-COST-011: Every source or account MUST carry a `billing_mode` of `api_billed` or `subscription` (decision D-007).
- FR-COST-012: Cost rollups, query APIs, and dashboards MUST expose actual API spend and subscription-equivalent value as two separate metrics and MUST never merge them into a single total.
- FR-COST-013: Subscription usage MUST be valued using equivalent API rates and MUST be labeled as subscription-equivalent value wherever it is displayed.

## 12.9 Limits and Quotas

- FR-LIMIT-001: Limit window kinds MUST remain provider-defined opaque identifiers.
- FR-LIMIT-002: Limit snapshots MUST support provider, account, organization, model family, source, and machine dimensions.
- FR-LIMIT-003: Reset time, utilization, remaining amount, limit amount, and unit SHOULD be supported.
- FR-LIMIT-004: Official and estimated limit data MUST remain distinguishable.
- FR-LIMIT-005: Multiple account or subscription limit streams MUST not be merged without an explicit grouping rule.
- FR-LIMIT-006: The system SHOULD support API rate-limit snapshots and subscription windows.
- FR-LIMIT-007: Limit alerts MUST support warning and critical thresholds.
- FR-LIMIT-008: Forecasting MUST identify the source data and confidence.
- FR-LIMIT-009: Unknown provider windows MUST not require schema migrations.
- FR-LIMIT-010: Direct gateway exporters MAY submit observed rate-limit headers as optional snapshots.
- FR-LIMIT-011: The initial release MUST implement subscription-limit sources for Anthropic (OAuth usage endpoint), OpenAI/Codex, and Z.ai coding plans (decision D-008).
- FR-LIMIT-012: A provider window registry MUST describe window kinds, display labels, and period semantics so dashboards and alerts do not hardcode provider window names.
- FR-LIMIT-013: Limit sources MUST degrade gracefully when an undocumented provider endpoint changes, surfacing a data quality event instead of failing collection.

## 12.10 Sessions, Logical Requests, and Attempts

- FR-TRACE-001: Sessions MUST group logical requests.
- FR-TRACE-002: Logical requests MUST group one or more attempts.
- FR-TRACE-003: Attempts MUST remain independently billable.
- FR-TRACE-004: The winning attempt MUST be identifiable.
- FR-TRACE-005: Failed attempts with cost MUST remain visible.
- FR-TRACE-006: Routing policy and fallback trigger MUST be queryable.
- FR-TRACE-007: Logical request summaries MUST not add token or cost totals to attempt totals.
- FR-TRACE-008: Trace IDs and span IDs SHOULD be supported for OpenTelemetry interoperability.
- FR-TRACE-009: Parent-child relationships SHOULD support agents and subagents.
- FR-TRACE-010: Existing Claude Code session aggregation MUST remain compatible.
- FR-TRACE-011: Session identity collisions across providers or sources MUST be prevented through scoped identity.
- FR-TRACE-012: Session rollups SHOULD include attempt count, fallback count, latency, tokens, and cost.

## 12.11 Tool, Reasoning, Cache, and Multimodal Dimensions

- FR-DIM-001: Reasoning tokens MUST be separately queryable.
- FR-DIM-002: Reasoning text MUST never be required or stored.
- FR-DIM-003: Tool-call count MUST be queryable.
- FR-DIM-004: Tool names MUST be omitted by default. Deployments MAY enable a bounded per-event tool-name histogram (for example `{"Bash": 5, "Read": 12}`) through explicit server-side opt-in configuration; entries MUST be bounded in count and length and MUST be covered by prohibited-key fuzz tests (decision D-005).
- FR-DIM-005: Cache reads, short writes, and long writes MUST remain separately queryable.
- FR-DIM-006: Providers with one cache-write category MUST not be falsely represented as Anthropic TTL categories without metadata.
- FR-DIM-007: Multimodal usage MUST use explicit unit records, not token fields.
- FR-DIM-008: Hosted tool request charges MUST be represented as billable units.
- FR-DIM-009: Provider-specific dimensions MUST be namespaced and bounded.
- FR-DIM-010: Dashboard labels MUST distinguish zero, unavailable, and unsupported values.

## 12.12 Query API

Recommended v2 query resources:

```text
GET /api/v2/usage
GET /api/v2/costs
GET /api/v2/requests
GET /api/v2/attempts
GET /api/v2/sessions
GET /api/v2/providers
GET /api/v2/models
GET /api/v2/sources
GET /api/v2/limits
GET /api/v2/data-quality
GET /api/v2/pricing
GET /api/v2/rollups
```

Requirements:

- FR-QUERY-001: Query APIs MUST support time range.
- FR-QUERY-002: Query APIs MUST support provider, model, source, machine, project, session, agent, environment, and outcome filters where applicable.
- FR-QUERY-003: Pagination MUST be stable.
- FR-QUERY-004: Sort order MUST be explicit.
- FR-QUERY-005: Aggregation grain MUST be selectable where safe.
- FR-QUERY-006: Cost responses MUST identify cost status and pricing version.
- FR-QUERY-007: Usage responses MUST distinguish attempts and logical requests.
- FR-QUERY-008: APIs MUST avoid accidental double counting.
- FR-QUERY-009: CSV export SHOULD be supported.
- FR-QUERY-010: Query responses SHOULD include data-quality warnings.
- FR-QUERY-011: Unknown provider and unknown model filters MUST be supported.
- FR-QUERY-012: Existing v1 query APIs MUST remain during migration.
- FR-QUERY-013: API clients MUST be documented.
- FR-QUERY-014: Grafana-compatible database views SHOULD be provided for common use cases.

## 12.13 Rollups

- FR-ROLLUP-001: Rollups MUST derive from final attempt events.
- FR-ROLLUP-002: Snapshots MUST not be counted after a final event exists.
- FR-ROLLUP-003: Logical summaries MUST not contribute billable usage.
- FR-ROLLUP-004: Daily rollups MUST preserve provider, model, machine, project, and provenance.
- FR-ROLLUP-005: Additional rollups SHOULD support source, environment, team, and cost center.
- FR-ROLLUP-006: Rollups MUST support reasoning and cache dimensions.
- FR-ROLLUP-007: Cost rollups MUST distinguish authoritative, partial, estimated, and unpriced.
- FR-ROLLUP-008: Rollup refresh MUST be idempotent.
- FR-ROLLUP-009: Correction events MUST trigger affected-period recomputation.
- FR-ROLLUP-010: Retention policies MUST not delete required source events before aggregates are verified.
- FR-ROLLUP-011: Rollup schema evolution MUST not break Grafana views without a migration plan.
- FR-ROLLUP-012: Performance benchmarks MUST cover high-cardinality dimensions.

## 12.14 Dashboard

### Required Dashboard Areas

1. Overview.
2. Usage.
3. Cost.
4. Providers and models.
5. Requests and attempts.
6. Sessions and agents.
7. Limits.
8. Sources and machines.
9. Data quality.
10. Pricing administration.

Requirements:

- FR-UI-001: Overview MUST support all providers, not only Claude.
- FR-UI-002: Provider and model filters MUST be global.
- FR-UI-003: Cost and token charts MUST distinguish cache, reasoning, input, and output.
- FR-UI-004: Request detail MUST show logical request and attempts.
- FR-UI-005: Fallback chains MUST be visualized.
- FR-UI-006: Latency and time-to-first-token MUST be chartable.
- FR-UI-007: Failure rate by provider and model MUST be chartable.
- FR-UI-008: Unpriced events and unknown models MUST be visible.
- FR-UI-009: Source freshness and schema-version drift MUST be visible.
- FR-UI-010: Provider-specific limit windows MUST remain supported.
- FR-UI-011: No prompt or response content UI is required.
- FR-UI-012: Dashboard labels MUST indicate official versus estimated values.
- FR-UI-013: Cost reconciliation drift SHOULD be visualized.
- FR-UI-014: Existing Claude-focused views MUST be generalized without removing useful limit-centric behavior.
- FR-UI-015: User-configurable saved filters MAY be added after the first release.

## 12.15 Alerts

- FR-ALERT-001: Existing spend and limit alerts MUST continue to work.
- FR-ALERT-002: Alerts MUST support provider, model, source, project, and environment filters.
- FR-ALERT-003: Add stale-source alerts.
- FR-ALERT-004: Add unpriced-event alerts.
- FR-ALERT-005: Add unknown-model alerts.
- FR-ALERT-006: Add exporter schema-drift alerts.
- FR-ALERT-007: Add failure-rate and latency alerts.
- FR-ALERT-008: Add fallback-rate alerts.
- FR-ALERT-009: Alert cooldown and quiet-hours behavior MUST remain supported.
- FR-ALERT-010: Alert context MUST not include content data.

## 12.16 Authentication and Authorization

- FR-SEC-001: API tokens MUST be hashed at rest.
- FR-SEC-002: Plaintext tokens MUST be shown only at creation.
- FR-SEC-003: Tokens MUST support scopes.
- FR-SEC-004: Tokens MUST support source or source-group restrictions.
- FR-SEC-005: Tokens MUST support revocation.
- FR-SEC-006: Last-used timestamps MUST be retained.
- FR-SEC-007: Administrative endpoints MUST require administrative scopes.
- FR-SEC-008: Bootstrap token behavior MUST remain available for first-run setup.
- FR-SEC-009: Token rotation MUST be documented.
- FR-SEC-010: Authentication failures MUST not reveal whether a token label exists.
- FR-SEC-011: Optional organization or tenant boundaries MAY be added in a later release, but schema choices MUST not prevent them.

## 12.17 Privacy and Data Governance

- FR-PRIV-001: Standard ingest MUST reject prompt and response content fields.
- FR-PRIV-002: Tool arguments, file paths, code snippets, and reasoning content MUST be prohibited.
- FR-PRIV-003: Machine, project, session, and agent identifiers MUST be classified as potentially identifying metadata.
- FR-PRIV-004: Deployments MUST be able to omit or pseudonymize those identifiers at the exporter.
- FR-PRIV-005: Tokemetry MUST document all stored fields.
- FR-PRIV-006: Retention policies MUST be configurable.
- FR-PRIV-007: Deletion by source, machine, project, or time range SHOULD be supported administratively.
- FR-PRIV-008: Daily aggregates MAY be retained longer than raw attempts.
- FR-PRIV-009: Audit logs MUST record administrative deletions and repricing.
- FR-PRIV-010: Backups MUST follow the same documented retention policy.
- FR-PRIV-011: API responses MUST avoid exposing internal token hashes or secrets.
- FR-PRIV-012: Privacy tests MUST include prohibited-key fuzzing.

## 12.18 Retention

Recommended defaults:

- Raw final attempt events: 180 days.
- Snapshots superseded by final events: 7 days or immediate compaction after verification.
- Daily rollups: indefinite.
- Limit snapshots: 400 days.
- Ingest batch metadata: 30 days.
- Security audit records: 400 days.
- Administrative correction records: indefinite.
- Alert events: 400 days.

Requirements:

- FR-RET-001: Retention MUST be configurable.
- FR-RET-002: Retention jobs MUST be incremental and resumable.
- FR-RET-003: Retention MUST not break referential integrity.
- FR-RET-004: Rollups MUST be verified before raw deletion.
- FR-RET-005: Retention status MUST be visible operationally.
- FR-RET-006: Legal hold or retention disable SHOULD be supported.
- FR-RET-007: SQLite development deployments and Postgres production deployments MUST behave consistently.

## 12.19 OpenTelemetry Interoperability

- FR-OTEL-001: Event schema SHOULD include trace ID and span ID.
- FR-OTEL-002: Provider, model, token usage, latency, and outcome SHOULD map to OpenTelemetry generative AI semantic conventions where stable.
- FR-OTEL-003: Tokemetry MUST implement a feature-flagged OTLP receiver that converts OpenTelemetry generative AI spans into v2 attempt events (decision D-009; implemented in Epic TOK-11).
- FR-OTEL-004: Initial v2 ingest MUST not depend on OTLP.
- FR-OTEL-005: Export to an external OpenTelemetry backend MAY be added later.
- FR-OTEL-006: Semantic-convention version MUST be recorded when mappings are used.
- FR-OTEL-007: Content attributes MUST remain disabled by default.

---

## 13. Non-Functional Requirements

### 13.1 Performance

- NFR-PERF-001: Ingest p95 target <= 200 ms for a 100-event batch under nominal load.
- NFR-PERF-002: The server SHOULD sustain at least 1,000 events per second on documented reference hardware.
- NFR-PERF-003: Query p95 target <= 500 ms for common 30-day aggregated views.
- NFR-PERF-004: Raw high-cardinality queries MUST require bounded time ranges.
- NFR-PERF-005: Cost calculation SHOULD be asynchronous if synchronous pricing materially affects ingest.
- NFR-PERF-006: Rollup jobs MUST be incremental.

### 13.2 Reliability

- NFR-REL-001: Ingest MUST be transactional.
- NFR-REL-002: Replay MUST be safe.
- NFR-REL-003: Database migrations MUST be reversible where practical.
- NFR-REL-004: Backups and restore tests MUST be automated.
- NFR-REL-005: Schema migration MUST support rolling upgrade constraints documented for self-hosted deployment.
- NFR-REL-006: Invalid price data MUST not corrupt usage data.
- NFR-REL-007: Failure of cost computation MUST not reject usage ingest.
- NFR-REL-008: WebSocket publication failure MUST not roll back accepted ingest.

### 13.3 Security

- NFR-SEC-001: No critical or high container or dependency scan findings.
- NFR-SEC-002: Database credentials and API tokens MUST be secret-managed.
- NFR-SEC-003: Rate limiting MUST protect ingest and query endpoints.
- NFR-SEC-004: Request size and JSON depth limits MUST be enforced.
- NFR-SEC-005: Administrative actions MUST be auditable.
- NFR-SEC-006: Public deployment guidance MUST require TLS.
- NFR-SEC-007: CORS defaults MUST be restrictive.
- NFR-SEC-008: WebSocket authentication MUST match REST authorization.

### 13.4 Maintainability

- NFR-MAIN-001: Core models MUST be provider-neutral.
- NFR-MAIN-002: Provider pricing strategies MUST be plugins.
- NFR-MAIN-003: Strict type checking and existing quality gates MUST pass.
- NFR-MAIN-004: Database migrations MUST have upgrade and downgrade tests.
- NFR-MAIN-005: API schemas MUST be generated and documented.
- NFR-MAIN-006: Dashboard components MUST not hardcode provider sets.
- NFR-MAIN-007: Test fixtures MUST include all initial providers.

---

## 14. Suggested Database Evolution

Potential new or evolved tables:

```text
providers
models
model_aliases
sources
usage_events_v2
usage_event_revisions
logical_requests
billable_units
rate_cards
computed_costs
limit_snapshots        (evolved: account, organization, source dimensions)
daily_rollups          (evolved: new grain with source, environment, billing_mode)
ingest_batches
data_quality_events
api_tokens             (evolved: scopes, optional source allowlist)
alert_rules
alert_events
audit_log
usage_events           (becomes a read-only v1 compatibility view, decision D-001)
```

Sessions remain query-derived (no physical `sessions` ledger requirement); session identity becomes scoped by provider and source per FR-TRACE-011.

### Key Schema Notes

- `usage_events` stores the active normalized event state.
- `usage_event_revisions` stores correction and conflict history.
- `logical_requests` stores non-billable grouping information.
- `billable_units` stores non-core unit counts and future multimodal usage.
- `rate_cards` replaces or extends fixed token price rows.
- `computed_costs` stores calculation results separately from usage facts.
- `sources` separates exporter identity from machine identity.
- `ingest_batches` supports operational traceability.
- `data_quality_events` records unknown providers, models, schema drift, conflicts, and unpriced usage.

Implementation may stage these changes instead of delivering all tables in one migration.

---

## 15. Suggested Repository Structure

```text
packages/core/src/tokemetry_core/
  models/
    usage_v1.py
    usage_v2.py
    pricing.py
    providers.py
    sources.py
  providers/
    registry.py
    anthropic.py
    openai.py
    zai.py
  pricing/
    strategies/
      anthropic.py
      openai.py
      zai.py
      generic.py
    sources/
      curated.py
      litellm.py
  schemas/
    common.py
    extensions.py

apps/server/src/tokemetry_server/
  api/v1/
  api/v2/
  services/
    ingest_v2.py
    revisions.py
    pricing.py
    costs.py
    sources.py
    data_quality.py
    retention.py
  db/
    models.py
    migrations/

apps/dashboard/src/
  features/
    overview/
    usage/
    costs/
    requests/
    providers/
    sources/
    limits/
    data-quality/
    pricing/

packages/clients/
  python/
  typescript/

docs/
  architecture/
    provider-neutral-baseline.md
    event-model-v2.md
    pricing-v2.md
    source-health.md
  api/
    ingest-v2.md
    query-v2.md
  integrations/
    ai-provider-proxy.md
```

---

## 16. Implementation Epics

## Epic TOK-1: Provider-Neutral Baseline

**Objective:** Audit current schemas, APIs, storage, pricing, collector behavior, and dashboard assumptions.

Acceptance criteria:

- Baseline document is committed.
- V1 compatibility requirements are explicit.
- Database migration constraints are documented.
- Existing tests are mapped to future epics.

Dependencies: none.

## Epic TOK-2: Provider and Model Registries

**Objective:** Introduce canonical providers, aliases, models, and unknown-model handling.

Acceptance criteria:

- Anthropic, OpenAI, and Z.ai are registered.
- Provider aliases normalize centrally.
- Model IDs remain native and provider-scoped.
- Unknown provider and model data quality records are generated.
- Existing Claude data remains queryable.

Dependencies: TOK-1.

## Epic TOK-3: Usage Event v2 and Ingest API

**Objective:** Add v2 event lifecycle, validation, and ingest while retaining v1.

Acceptance criteria:

- V2 schema and OpenAPI are published.
- Attempts, logical request IDs, finality, sequence, outcomes, source, routing, and reasoning are supported.
- V1 events map into internal v2 representation.
- V1 endpoints retain behavior.
- Validation endpoint exists.
- Batch tests cover duplicates, snapshots, finality, and conflicts.

Dependencies: TOK-1, TOK-2.

## Epic TOK-4: Source Registry and Scoped Tokens

**Objective:** Represent gateways and collectors as first-class reporting sources.

Acceptance criteria:

- Source type and version are stored.
- Ingest tokens have scopes.
- Sources have freshness and health status.
- Existing machine tracking remains compatible.
- aiProviderProxy can receive an ingest-only token.

Dependencies: TOK-3.

## Epic TOK-5: Pricing and Cost Engine v2

**Objective:** Support provider-neutral rate cards and multiple billable units.

Acceptance criteria:

- Existing pricing migrates without historical change.
- Anthropic, OpenAI, and Z.ai rate cards are supported.
- Missing prices produce unpriced status.
- Cost records retain pricing version and source.
- Search/tool request charges and cached input can be represented.
- Dry-run price import and diff exist.

Dependencies: TOK-2, TOK-3.

## Epic TOK-6: aiProviderProxy Integration

**Objective:** Provide a production integration contract and verify direct exporter ingest.

Acceptance criteria:

- Integration documentation is complete.
- TypeScript client or examples are available.
- aiProviderProxy attempt events ingest successfully.
- Retries and fallback attempts do not double count.
- Requested and routed model are visible.
- Content-free payload tests pass.
- Source freshness is visible.

Dependencies: TOK-3, TOK-4, TOK-5.

## Epic TOK-7: Query API and Rollups v2

**Objective:** Expose provider-neutral usage, cost, request, attempt, source, and quality queries.

Acceptance criteria:

- V2 query endpoints exist.
- Attempt and logical request semantics prevent double counting.
- Cache and reasoning usage are queryable.
- Cost status is visible.
- Rollups include initial provider-neutral dimensions.
- Existing v1 query endpoints remain functional.

Dependencies: TOK-3, TOK-5.

## Epic TOK-8: Dashboard Generalization

**Objective:** Replace Claude-only assumptions with provider-neutral views while preserving limit-centric functionality.

Acceptance criteria:

- Global provider and model filters exist.
- Requests and fallback chains are visible.
- Source health and data quality are visible.
- Unknown and unpriced usage are visible.
- Cache, reasoning, input, and output are distinguishable.
- Existing Claude limit views remain useful.

Dependencies: TOK-4, TOK-7.

## Epic TOK-9: Alerts and Data Quality

**Objective:** Add operational alerts for reporting and accounting failures.

Acceptance criteria:

- Stale source alerts work.
- Unknown model and unpriced usage alerts work.
- Failure, latency, and fallback-rate alerts work.
- Alert context remains content-free.
- Existing alert delivery channels remain supported.

Dependencies: TOK-4, TOK-5, TOK-7.

## Epic TOK-10: Retention, Security, and Operations

**Objective:** Production hardening.

Acceptance criteria:

- Configurable retention exists.
- Backup and restore tests pass.
- Scoped token rotation is documented.
- Rate limits and request bounds are enforced.
- Audit log covers administrative actions.
- Migration and rollback runbooks exist.
- Performance and security gates pass.

Dependencies: all prior epics.

## Epic TOK-11: OpenTelemetry Interoperability

**Objective:** Align v2 concepts with generative AI semantic conventions.

Acceptance criteria:

- Trace and span IDs are supported.
- A mapping document exists and records the pinned semantic-convention version.
- Content attributes remain disabled by default.
- A feature-flagged OTLP receiver converts generative AI spans into v2 attempt events.

Dependencies: TOK-3, TOK-7. The receiver is feature-flagged and MAY be enabled after initial general availability.

## Epic TOK-12: Multi-Provider Limit Sources

**Objective:** Track subscription and rate-limit windows for Anthropic, OpenAI/Codex, and Z.ai through a provider-neutral window registry.

Acceptance criteria:

- A window registry describes provider window kinds, labels, and periods.
- Limit snapshots support account, organization, and source dimensions.
- The existing Anthropic OAuth limits source keeps working unchanged.
- An OpenAI/Codex limits source reports subscription windows.
- A Z.ai coding-plan limits source reports subscription windows.
- Gateway-observed rate-limit headers can be ingested as optional snapshots.
- Dashboards and alerts read window metadata from the registry, not hardcoded labels.

Dependencies: TOK-3, TOK-4.

---

## 17. Development Sequence

Recommended sequence:

1. Baseline.
2. Provider and model registries.
3. Usage Event v2 and ingest.
4. Source registry and scoped tokens.
5. Pricing and cost v2.
6. aiProviderProxy integration.
7. Query APIs and rollups.
8. Dashboard.
9. Alerts and data quality.
10. Multi-provider limit sources.
11. Retention, security, and operations.
12. OpenTelemetry interoperability.

The initial usable vertical slice is complete after step 6.

Task Master mapping: steps 1 through 12 correspond to parent tasks 60 through 71 in the `master` tag (TOK-1 through TOK-9, TOK-12, TOK-10, TOK-11 in that order).

---

## 18. Testing Strategy

### 18.1 Unit Tests

- Provider alias normalization.
- Model lookup.
- V1-to-v2 conversion.
- Event validation.
- Snapshot and finality resolution.
- Conflict and correction behavior.
- Pricing selection.
- Decimal cost computation.
- Billable unit aggregation.
- Scope authorization.
- Retention eligibility.

### 18.2 Integration Tests

- V1 collector ingest.
- V2 aiProviderProxy ingest.
- Duplicate batches.
- Snapshot followed by final.
- Conflicting same-sequence events.
- Unknown provider and unknown model.
- Missing pricing.
- Pricing update and historical cost stability.
- Token scope enforcement.
- Rollup refresh.
- WebSocket publication.

### 18.3 Migration Tests

- Existing SQLite development database.
- Existing Postgres production database.
- Upgrade and downgrade where supported.
- Pricing-row migration.
- Existing Claude Code sessions and rollups.
- Existing API tokens.
- Existing dashboards and queries.

### 18.4 End-to-End Tests

- Claude Code collector to server to dashboard.
- aiProviderProxy Anthropic event to server to dashboard.
- aiProviderProxy OpenAI event to server to dashboard.
- aiProviderProxy Z.ai event to server to dashboard.
- Fallback request with multiple attempts.
- Unpriced model workflow.
- Limit snapshot and alert.

### 18.5 Performance Tests

- 100-event batch ingest.
- 5,000-event compatibility batch.
- 1,000 events per second sustained load.
- High-cardinality project and session dimensions.
- 30-day dashboard queries.
- Rollup recomputation after correction.
- Retention job on production-scale tables.

### 18.6 Security and Privacy Tests

- Prohibited content keys.
- Oversized metadata.
- Deeply nested JSON.
- Token scope bypass.
- Revoked token.
- WebSocket authorization.
- Secret redaction.
- Administrative audit records.
- Pseudonymized identifiers.
- SQL and filter injection.

---

## 19. Release Acceptance Criteria

- AC-001: Existing Claude Code collectors continue to ingest without modification.
- AC-002: aiProviderProxy exports Anthropic, OpenAI, and Z.ai attempt events successfully.
- AC-003: Replay and duplicate batches do not inflate usage.
- AC-004: Streaming snapshots resolve correctly to final events.
- AC-005: Fallback attempts are independently visible and correctly grouped.
- AC-006: Logical request views do not double-count cost.
- AC-007: Historical cost remains stable after new prices are added.
- AC-008: Unknown models and unpriced events are visible.
- AC-009: Provider-neutral dashboard filters work.
- AC-010: Source health identifies stale exporters.
- AC-011: Standard ingest rejects prohibited content.
- AC-012: Scoped ingest-only tokens cannot access query or admin endpoints.
- AC-013: Database upgrade succeeds on representative existing deployments.
- AC-014: Backup and restore tests pass.
- AC-015: Performance targets are measured and acceptable.
- AC-016: No unresolved critical or high-severity security findings remain.
- AC-017: API and integration documentation is complete.
- AC-018: Rollback procedure is documented.
- AC-019: Actual API spend and subscription-equivalent value are visible as separate metrics in rollups, query APIs, and dashboards.
- AC-020: Limit windows from Anthropic, OpenAI/Codex, and Z.ai sources are visible with registry-driven labels.
- AC-021: The feature-flagged OTLP receiver converts generative AI semantic-convention spans into v2 attempt events in an end-to-end test.
- AC-022: Tool-name histograms are rejected unless server-side opt-in is enabled, and accepted bounded histograms never contain prohibited content keys.

---

## 20. Migration Strategy

### Phase 0: Baseline and Schema Design

No production behavior changes.

### Phase 1: Internal Registries

Add provider and model registries while retaining v1 schemas and APIs.

### Phase 2: V2 Ingest Behind Feature Flag

Deploy v2 tables or columns and v2 endpoints. Existing collectors remain on v1.

### Phase 3: aiProviderProxy Dual Write

Enable selected proxy instances. Compare proxy-observed cost and Tokemetry-computed cost.

### Phase 4: Provider-Neutral Queries

Release v2 query APIs and generalized rollups.

### Phase 5: Dashboard Generalization

Switch UI to provider-neutral APIs while preserving current Claude-specific limit functions.

### Phase 6: Retention and Operational Hardening

Enable retention only after rollup verification and backup testing.

### Phase 7: V1 Deprecation Planning

Do not remove v1 until collector migration, compatibility period, and explicit deprecation policy are complete.

---

## 21. Risks and Mitigations

### R-001: Schema Over-Generalization

**Risk:** Flexible metadata becomes unqueryable.  
**Mitigation:** Common dimensions remain typed columns; extensions are namespaced and bounded.

### R-002: Double Counting

**Risk:** Snapshots, retries, and logical summaries inflate totals.  
**Mitigation:** Finality and event-kind semantics; rollups use final attempt events only.

### R-003: Pricing Complexity

**Risk:** Provider pricing cannot be represented by fixed token columns.  
**Mitigation:** Rate cards and billable units; retain simple PriceRow compatibility.

### R-004: Migration Breakage

**Risk:** Existing data or dashboard queries fail.  
**Mitigation:** V1 compatibility, staged migrations, upgrade tests, compatibility views.

### R-005: High Cardinality

**Risk:** Session, agent, project, and request dimensions slow queries.  
**Mitigation:** Indexed bounded dimensions, rollups, time-range limits, performance testing.

### R-006: Privacy Creep

**Risk:** Integrators place content in `extra` or dimensions.  
**Mitigation:** Allowlist validation, prohibited-key detection, length limits, documentation, fuzz tests.

### R-007: Pricing Source Drift

**Risk:** Automated price feeds contain errors.  
**Mitigation:** Dry-run diff, source labeling, manual approval, effective dates, audit logs.

### R-008: Provider Semantic Drift

**Risk:** New cache or reasoning fields emerge.  
**Mitigation:** Typed provider extensions and versioned schemas.

### R-009: Source Clock Skew

**Risk:** Incorrect timestamps affect pricing and rollups.  
**Mitigation:** Clock-skew validation, source health warning, bounded future timestamps.

### R-010: Cost Recalculation Load

**Risk:** Repricing or corrections are expensive.  
**Mitigation:** Asynchronous jobs, affected-range recomputation, audit and progress tracking.

---

## 22. Resolved Decisions

All open questions were resolved with the product owner on 2026-07-12. Requirements elsewhere in this document reference these decisions by ID.

- D-001 (OQ-001): V2 uses a new ledger table `usage_events_v2` with a v1 backfill and a read-only compatibility view replacing the physical `usage_events` table. See Section 11 for the migration sequence.
- D-002 (OQ-002): Ingest-only clients receive no correction privileges. Final-to-final changes require the `admin:corrections` scope; ingest-only tokens can only submit new events and snapshot revisions.
- D-003 (OQ-003): Logical request summaries are stored in a dedicated `logical_requests` table from the first release, populated automatically from attempt events.
- D-004 (OQ-004): Arbitrary dimensions use a configurable allowlist. Defaults: `team`, `cost_center`, `environment`. Bounds: at most 16 keys per event, key length 64, value length 256.
- D-005 (OQ-005): `tool_call_count` is always stored. A bounded per-event tool-name histogram is accepted only when the deployment enables it server-side; the default is off and raw tool names are otherwise rejected.
- D-006 (OQ-006): Rate cards use one generic `rate_cards` table (provider, native model, unit type, effective range, currency, region, tier, mode, context bracket, price, source, priority) plus a `billable_units` table for non-token units. Provider specificity lives in pricing strategy plugins, not tables.
- D-007 (OQ-013): Costs are presented as two explicit metrics everywhere: actual API spend (`billing_mode = api_billed`) and subscription-equivalent value (`billing_mode = subscription`). Totals are never silently mixed.
- D-008 (limits scope): The initial release implements limit sources for Anthropic (existing OAuth endpoint), OpenAI/Codex, and Z.ai coding plans, on top of a provider-neutral window registry (Epic TOK-12).
- D-009 (OTel scope): The OTLP receiver bridge is implemented in this program behind a feature flag (Epic TOK-11), not deferred.
- D-010 (OQ-007): Superseded snapshots are retained 7 days and then compacted.
- D-011 (OQ-008): Every v2 event MUST carry source identity in the payload; sources auto-register on first sight. Binding tokens to specific sources is an optional per-token allowlist, not mandatory.
- D-012 (OQ-009): Python and TypeScript clients are generated from the OpenAPI schema with thin hand-written wrappers for auth, batching, and retry, published under `packages/clients/`.
- D-013 (OQ-010): The OpenTelemetry generative AI semantic-convention version is pinned at implementation time and recorded on every mapped event and in the mapping document.
- D-014 (OQ-011): No organization or tenant isolation in this release. Schema choices must not preclude adding it later; this is a documented review item on every new table.
- D-015 (OQ-012): All pricing imports, including automated LiteLLM sync, produce a dry-run diff that requires explicit administrative apply. Applies are audited.
- D-016 (OQ-014): Exporter-observed cost is retained per attempt as reconciliation metadata. A full provider-invoice reconciliation feed is deferred until after general availability.
- D-017 (OQ-015): No legacy dashboard routes are removed and no redirects are added; existing routes remain and new provider-neutral pages extend the navigation.
- D-018 (task structure): Task Master decomposition uses one parent task per epic, IDs 60 through 71 in the `master` tag, with subtasks sized to one full quality-gated workflow unit each.

---

## 23. Task Master Decomposition Guidance

Task generation should follow these rules:

1. Create one parent task per epic.
2. Separate schema, migration, API, service, test, dashboard, and documentation subtasks.
3. Preserve V1 compatibility as explicit acceptance criteria in every affected epic.
4. Implement provider and model registries before provider-neutral dashboard work.
5. Implement event finality before enabling aiProviderProxy production export.
6. Implement unpriced-event visibility before calling cost support complete.
7. Treat pricing migration and cost reconciliation as separate tasks.
8. Add migration tests to every database-changing task.
9. Add privacy validation to every ingest or metadata task.
10. Preserve requirement IDs in generated tasks and pull requests.
11. Do not combine dashboard generalization with core ingest changes in one task.
12. Treat OpenTelemetry as a separate milestone unless required by the initial release.
13. Parent tasks are Task Master IDs 60 through 71 in the `master` tag, mapped per Section 17. Every subtask carries verbose implementation details and a test strategy and is sized to one full quality-gated workflow unit (implement, document, test, lint, type-check, scan, review, commit).

---

## 24. Reference Sources

Implementation should verify current versions of:

- Tokemetry repository, core models, ingest API, collector, database models, pricing strategies, queries, and dashboard.
- aiProviderProxy canonical event and exporter contract.
- Anthropic usage, caching, limits, service tiers, and pricing.
- OpenAI Responses usage, reasoning, prompt caching, tools, and pricing.
- Z.ai GLM usage, caching, reasoning, tools, limits, and pricing.
- OpenTelemetry generative AI semantic conventions.
- LiteLLM pricing and provider registry patterns.
- Helicone, Langfuse, Portkey, OpenLLMetry, MLflow tracing, and other open-source AI observability systems.

Official provider documentation takes precedence.

---

## 25. Definition of Done

A requirement is complete only when:

- Code and migration are merged.
- Upgrade tests pass.
- V1 compatibility is verified where applicable.
- Unit, integration, and end-to-end tests pass.
- API and user documentation are updated.
- Privacy and authorization tests pass.
- Operational metrics and failure modes are documented.
- Acceptance criteria are demonstrated.
- Requirement IDs are referenced in implementation work.
