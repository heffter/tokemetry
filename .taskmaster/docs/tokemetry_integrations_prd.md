# PRD Extension: Tokemetry Integrations and Control Plane

**Document ID:** PRD-TOK-003
**Status:** Draft for review
**Version:** 1.0
**Date:** 2026-07-22
**Repository:** `heffter/tokemetry`
**Extends:** PRD-TOK-002 (`tokemetry_ai_observability_prd.md`)
**Primary consumer:** Claude Task Master

---

## 1. Executive Summary

Tokemetry's next step is not more dashboards. It is becoming the self-hosted
control plane for AI usage across developer tools, application SDKs, gateways,
and finance. This extension adds seven epics on top of the provider-neutral
platform delivered under PRD-TOK-002:

- **TOK-13** LiteLLM exporter (`tokemetry-litellm` PyPI package): completed
  calls from the most widely deployed OSS gateway land in `/api/v2/ingest/events`.
- **TOK-14** Vercel AI SDK adapter (`@tokemetry/ai-sdk` npm package) plus OTLP
  receiver compatibility normalization for AI SDK attribute names and
  OTLP/protobuf payloads.
- **TOK-15** Provider billing reconciliation: scheduled read-only imports of
  provider-reported usage and invoice-aligned cost (OpenAI first, Anthropic
  second) reconciled against tokemetry-computed cost.
- **TOK-16** Generic signed outbound webhooks with Slack and Microsoft Teams
  presets, plugged into the existing alert engine.
- **TOK-17** Prometheus/OpenMetrics `/metrics` endpoint for operational health.
- **TOK-18** OIDC authentication and role-based access control for the
  dashboard, coexisting with existing scoped bearer tokens.
- **TOK-19** Budgets by project/provider with linear burn-rate forecast alerts
  and ownership metadata.

Everything builds on what is already strong: the v2 event model (attempts,
`observed_cost`, `billable_units`, trace IDs), the source registry and health
model, rate cards and reconciliation, the notifier abstraction with hot
reconfiguration, and the feature-flagged OTLP receiver. All seven epics are
greenfield: no webhook, metrics, OIDC, billing-import, LiteLLM, or AI SDK
code exists in the repository today.

Non-negotiable constraints carried forward from PRD-TOK-002: self-hosted and
privacy-first (metadata-only, content never stored), provider-neutral OOP
abstractions with a fake provider proving each new interface, and full V1/V2
API compatibility for existing collectors and clients.

---

## 2. Strategic Rationale

The platform already normalizes usage from collectors, gateways, and OTel
spans, but adoption requires meeting users where their traffic already flows:

- **Gateways:** one LiteLLM callback captures OpenAI, Anthropic, Bedrock,
  Azure, and 100+ providers without building a collector per provider.
- **Application SDKs:** the Vercel AI SDK emits OpenTelemetry spans natively;
  tokemetry's OTLP receiver is nearly the right foundation, and a turnkey
  adapter removes the remaining setup friction.
- **Finance:** observed-versus-computed reconciliation exists per event, but
  most deployments will never populate `observed_cost` per request. Importing
  provider-reported usage and cost closes the loop with the invoice.
- **Operations:** SRE teams need `/metrics` scraping and webhook incident
  routing into existing on-call tooling, not database access for Grafana.
- **Teams:** token-gate auth is right for a personal deployment and weak for
  teams; OIDC, roles, scoped visibility, and budgets make tokemetry adoptable
  by a group without weakening machine ingest.

---

## 3. Relationship to PRD-TOK-002 and Current State

### 3.1 Foundations already in place

Verified against the repository as of 2026-07-22 (commit `385f229`):

- **v2 ingest** (`apps/server/src/tokemetry_server/api/v2/ingest.py`,
  `services/ingest_v2.py`): `POST /api/v2/ingest/events|validate|limits|aggregates`,
  batch envelope with gzip, idempotent `(provider, event_id)` identity,
  finality/sequence revision resolution (`services/revisions.py`), persistence
  to `usage_events_v2`, `usage_event_revisions`, `billable_units`,
  `ingest_batches`, `sources`.
- **v2 event model** (`packages/core/src/tokemetry_core/usage_v2.py`):
  `UsageEventV2` with token counters (input, output, cache read, cache write
  short/long, reasoning), outcome and latency fields, `Routing`, `SourceRef`,
  `dimensions`, `billable_units`, `observed_cost`, OTel `trace_id`/`span_id`,
  `extra`. `AggregateImportV2` and `LimitSnapshotV2` exist for non-attempt data.
- **OTLP receiver** (`api/v2/otel.py`, `otel/receiver.py`, `otel/convert.py`,
  `otel/semconv.py`): feature-flagged (`otel_receiver_enabled`, default off),
  OTLP/JSON only, semconv 1.30.0, recognizes `gen_ai.system`,
  `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.operation.name`,
  `gen_ai.usage.{input_tokens,output_tokens,cache_read_tokens,reasoning_tokens}`,
  strips content attributes.
- **Alerting** (`services/alerting/`): `Notifier` ABC (`is_configured()`,
  `async send(title, body, severity)`), ntfy/Telegram/SMTP implementations,
  `AlertEngine` with cooldown, quiet hours, and `reconfigure()` hot swap,
  rule engine with evaluator registry (`rules.py`), channel settings stored in
  the `app_settings` KV table with DB-over-env precedence and secret masking
  (`services/channel_config.py`), management API in `api/alerts.py`.
- **Cost and reconciliation** (`services/cost_v2.py`, `services/pricing_v2.py`,
  `services/computed_costs.py`, `services/queries_v2.py`): versioned rate
  cards, computed cost records with status, `cost_reconciliation()` over
  events carrying `observed_cost`, `GET /api/v2/costs/reconciliation`.
- **Source health** (`services/sources.py`): auto-registration by
  `(type, name, instance_id)`, per-batch health fields, staleness thresholds.
- **Background jobs** (`app.py` lifespan): plain asyncio loops for alerts,
  cost sweeping, and retention, each gated by a settings flag. No
  APScheduler/Celery; new scheduled work follows this pattern.
- **Auth** (`api/auth.py`, `scopes.py`, `api/tokens.py`): hashed bearer
  tokens with scopes (`ingest:events`, `query:read`, `admin:*`), optional
  source allowlists, bootstrap token. The dashboard stores a token in local
  storage; there are no users, sessions, or roles.
- **Outbound HTTP:** one shared `httpx.AsyncClient` on `app.state.http_client`.
- **Config:** `Settings(BaseSettings)` with `TOKEMETRY_` env prefix; Alembic
  migrations `db/migrations/versions/0001..0027` (zero-padded sequential).
- **Health endpoints:** `GET /api/v1/health` (liveness) and
  `GET /api/v2/ready` (readiness with DB and migration status). No `/metrics`.

### 3.2 Greenfield confirmation

No implementation exists for any epic in this document. The only related
artifacts are the OTLP receiver (extended, not replaced, by TOK-14) and the
per-event `observed_cost` reconciliation (complemented, not replaced, by
TOK-15).

---

## 4. Goals

- G-101: Ingest LiteLLM SDK and proxy traffic through an official, published
  callback package with batch delivery and at-least-once safety.
- G-102: Provide a turnkey, privacy-safe Vercel AI SDK integration and make
  the OTLP receiver accept real-world AI SDK telemetry (attribute dialects
  and OTLP/protobuf).
- G-103: Import provider-reported usage and cost on a schedule behind a
  provider-neutral importer abstraction, and reconcile three ways: computed,
  observed, provider-billed.
- G-104: Route alerts into arbitrary engineering tooling via signed generic
  webhooks with Slack and Teams presets, without new per-tool transports.
- G-105: Expose operational health as Prometheus metrics scrapeable without
  database access.
- G-106: Support team deployments with OIDC login, roles, and scoped
  visibility while keeping machine ingest on bearer tokens unchanged.
- G-107: Enforce and forecast budgets by project and provider.
- G-108: Keep every addition provider-neutral, metadata-only, and
  backward-compatible; prove each new abstraction with a fake implementation.

## 5. Non-Goals

- NG-101: Tokemetry does not proxy, route, or execute model requests
  (unchanged from PRD-TOK-002).
- NG-102: No storage of prompt/response content, tool arguments, or reasoning
  text, in any integration path.
- NG-103: No billing enforcement (hard cutoffs of provider traffic); budgets
  alert and report, they do not block requests.
- NG-104: No multi-tenant SaaS features (per-org data partitions, billing of
  tokemetry itself); RBAC scopes visibility inside one deployment.
- NG-105: No bundled identity provider; OIDC integrates with an external IdP.
- NG-106: No Prometheus remote-write or long-term metrics storage; tokemetry
  exposes an exposition endpoint only.
- NG-107: Upstreaming the LiteLLM callback into the BerriAI repository is a
  stretch goal, not an acceptance criterion.

---

## 6. Delivery Phasing and Priority Order

Priority order (approved direction):

1. **Phase A — TOK-13** LiteLLM callback. Fastest path to multi-provider
   gateway traffic; exercises v2 ingest with an external producer.
2. **Phase B — TOK-14** AI SDK adapter and OTLP compatibility. Server-side
   normalization first, npm package second.
3. **Phase C — TOK-15** OpenAI billing reconciler, then Anthropic, behind the
   shared importer abstraction.
4. **Phase D — TOK-16 and TOK-17** Webhooks and Prometheus metrics. Mutually
   independent; may be parallelized.
5. **Phase E — TOK-18 then TOK-19** OIDC/RBAC, then budgets (budget admin UI
   assumes roles exist).

Each phase lands on `master` fully gated (tests, ruff, mypy strict, trivy,
docs) per the standard workflow. No epic may break V1 or V2 ingest clients.

---

## 7. Epic TOK-13: LiteLLM Exporter (`tokemetry-litellm`)

### 7.1 Design summary

A new Python package `packages/exporters/tokemetry-litellm/` (uv workspace
member, published to PyPI as `tokemetry-litellm`) implementing a LiteLLM
callback that maps `StandardLoggingPayload` to `UsageEventV2` and delivers
batches to `POST /api/v2/ingest/events`.

Research facts the design relies on (verified 2026-07; sources in Section 19):

- `CustomLogger` exposes sync and async success/failure hooks plus per-chunk
  stream hooks; for streams the success hook fires exactly once after stream
  end with the aggregated response. LiteLLM computes `response_cost` for
  streams and non-streams alike from its community pricing map.
- `CustomBatchLogger` provides an in-memory queue (default max 50,000), a
  periodic flush loop, and `async_send_batch()` as the subclass hook. It has
  no delivery retry/backoff of its own; the exporter must implement both.
- `StandardLoggingPayload` carries `id`, `trace_id`, `call_type`, `status`,
  `response_cost`, `cost_breakdown`, flat `prompt_tokens`/`completion_tokens`/
  `total_tokens`, timing (`startTime`, `endTime`, `completionStartTime`,
  `response_time`), `model`, `custom_llm_provider`, `cache_hit`, error info,
  and proxy metadata (key alias, team, org, project, end user, request tags).
  Cache and reasoning token counts appear only inside
  `metadata.usage_object` (`prompt_tokens_details.cached_tokens`,
  `cache_creation_tokens`, `completion_tokens_details.reasoning_tokens`).
- `messages`/`response` contain raw content by default; operators must set
  `litellm.turn_off_message_logging = True` for payload-level redaction.
- Registration: `litellm.callbacks = [TokemetryLogger()]` in SDK mode; module
  path reference under `litellm_settings.callbacks` in proxy `config.yaml`.
  Proxy `--num_workers` forks separate processes, each with its own queue.
- LiteLLM core is MIT, Python >=3.10, weekly releases (~v1.93 as of 2026-07).

Mapping decisions:

- `event_id` = payload `id`; provider = normalized `custom_llm_provider`
  (registry alias resolution server-side); `native_model` = `model`;
  `requested_model` = `model_group` when present.
- Tokens: `input_tokens` = `prompt_tokens` minus cached tokens when the
  provider folds cache reads into the prompt count (OpenAI convention),
  `cache_read_tokens` from `usage_object.prompt_tokens_details.cached_tokens`,
  `cache_write_short_tokens` from `cache_creation_tokens`,
  `reasoning_tokens` from `completion_tokens_details.reasoning_tokens`.
  Token-derivation rules are provider-conditional and unit-tested per
  provider family.
- `observed_cost` = `response_cost` with `provenance` marking it as
  gateway-computed (LiteLLM pricing map), not provider-billed. This feeds the
  existing reconciliation view and later three-way reconciliation (TOK-15).
- `trace_id` = payload `trace_id`; `provider_request_id` from hidden params
  when available; `streaming`, `latency_ms`, `time_to_first_token_ms` from
  timing fields; failures map to `success=False` with sanitized
  `outcome`/`http_status` (never tracebacks).
- Dimensions: `user_api_key_alias`, `user_api_key_team_alias`, `end_user`,
  `request_tags`, and `requester_metadata` keys pass through the exporter's
  configurable allowlist into `dimensions`. Machine/hostname and session ID
  have no native LiteLLM field; they enter via client `extra_body.metadata`
  and the same allowlist. `SourceRef(type=gateway, name="litellm",
  instance_id=<per-process UUID>)`.

Delivery: gzip batches, bearer token with `ingest:events` scope, exponential
backoff with jitter, bounded queue with drop-oldest overflow and a dropped
counter surfaced in exporter logs. Duplicate delivery is safe because ingest
is idempotent on `(provider, event_id)`; multi-worker proxies therefore need
no cross-process coordination.

### 7.2 Functional requirements

- FR-LLM-001: The exporter MUST subclass `CustomLogger` via
  `CustomBatchLogger` and implement async success and failure hooks.
- FR-LLM-002: The exporter MUST NOT read or forward `messages`, `response`,
  or any content-bearing field, independent of
  `turn_off_message_logging`; content keys are stripped before serialization.
- FR-LLM-003: The exporter MUST map payloads to schema_version 2 events per
  Section 7.1 and pass server-side validation (`/api/v2/ingest/validate` used
  in CI conformance tests).
- FR-LLM-004: Streamed calls MUST produce exactly one final event
  (finality=final, sequence=1); per-chunk hooks MUST NOT emit events.
- FR-LLM-005: `response_cost` MUST land in `observed_cost` with provenance
  identifying LiteLLM as a gateway-computed source.
- FR-LLM-006: Delivery MUST batch (configurable flush interval and batch
  size), gzip, retry with exponential backoff and jitter, and cap the queue;
  overflow MUST drop oldest and count drops.
- FR-LLM-007: Failure events MUST carry sanitized error taxonomy
  (`error_code`, `error_class`, `llm_provider`) and MUST NOT carry tracebacks
  or request content.
- FR-LLM-008: Configuration MUST come from constructor arguments and
  `TOKEMETRY_*` environment variables: endpoint URL, token, flush interval,
  batch size, queue cap, dimension allowlist, TLS verification.
- FR-LLM-009: The package MUST work in both SDK-callback and proxy
  `config.yaml` registration modes and document both.
- FR-LLM-010: The package MUST declare `litellm` as an optional/loose peer
  dependency range and pin a tested minimum; CI MUST run a conformance test
  against the pinned minimum and latest.
- FR-LLM-011: A fake/replay conformance harness MUST exist under the server
  integration tests exercising real HTTP against a test server instance.
- FR-LLM-012: Multi-worker proxy deployments MUST be documented: per-worker
  queues, idempotent ingest as the dedupe layer, per-process instance IDs.

### 7.3 Task decomposition (Task Master)

1. Package scaffold, settings, and wire model mapping (unit-tested pure
   mapper: payload dict to `UsageEventV2`).
2. Batch delivery client (queue, flush loop, gzip, retry/backoff, drop
   accounting) with fake-transport unit tests.
3. Provider-conditional token derivation rules and cost provenance mapping
   (per-provider unit-test matrix: OpenAI, Anthropic, Bedrock, Azure).
4. Failure-path events and privacy stripping (prohibited-key fuzz tests).
5. Server-side integration/conformance suite (proxy-style payload fixtures
   through `/api/v2/ingest/validate` and `/events`).
6. Docs: `docs/integrations/litellm.md` (SDK and proxy setup, privacy
   checklist including `turn_off_message_logging`, multi-worker notes) plus
   README and PyPI publishing workflow.

---

## 8. Epic TOK-14: Vercel AI SDK Adapter and OTLP Compatibility

### 8.1 Design summary

Two deliverables: (a) server-side OTLP receiver compatibility so real AI SDK
telemetry ingests correctly; (b) an npm package `@tokemetry/ai-sdk` at
`packages/exporters/tokemetry-ai-sdk/` providing turnkey wiring.

Research facts the design relies on:

- The AI SDK is at major version 7 (mid-2026). v7 moved OpenTelemetry
  support into `@ai-sdk/otel` and introduced global `registerTelemetry()`
  (opt-out thereafter); v4-v6 use per-call `experimental_telemetry` with
  `isEnabled` defaulting to false. `recordInputs`/`recordOutputs` default to
  true in the legacy surface; disabling them omits prompt/response content
  while keeping token counts.
- Span shape: root spans `ai.generateText|streamText|generateObject|...`
  containing per-round-trip `*.doGenerate`/`*.doStream` spans and nested
  `ai.toolCall` spans. Call-level spans carry `gen_ai.system`,
  `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`, plus legacy `ai.usage.promptTokens`/
  `ai.usage.completionTokens`; root spans carry only the legacy pair.
  `ai.telemetry.functionId` and `ai.telemetry.metadata.*` are the
  correlation dimensions.
- Known upstream gaps: cached/reasoning token attributes
  (`ai.usage.cachedInputTokens`, `ai.usage.reasoningTokens`) are documented
  but missing from `generateText`/`generateObject` spans (vercel/ai #12801),
  and OpenAI cache reads can be double-counted into input tokens (#8794).
  OTel-only ingestion is therefore lossy for cache/reasoning accounting
  until fixed upstream; the PRD treats those fields as best-effort.
- Standard OTel exporters default to OTLP/protobuf over HTTP; the receiver
  currently parses OTLP/JSON only. Prior art (Braintrust, pre-v7 Langfuse)
  ships the exporter/span-processor pattern, which works across v4-v7
  without depending on the SDK-internal telemetry registry.

Server-side normalization (`otel/semconv.py`, `otel/convert.py`):

- Accept OTLP/protobuf (`application/x-protobuf`) in addition to JSON on
  `POST /api/v2/otel/v1/traces`, using the `opentelemetry-proto` bindings.
- Token attribute dialects, first match wins per field:
  `gen_ai.usage.input_tokens` | `gen_ai.usage.prompt_tokens` |
  `ai.usage.inputTokens` | `ai.usage.promptTokens`; the analogous chain for
  output tokens; `gen_ai.usage.cache_read_tokens` | `gen_ai.usage.cached_input_tokens` |
  `ai.usage.cachedInputTokens`; `gen_ai.usage.reasoning_tokens` |
  `ai.usage.reasoningTokens`.
- Provider/model dialects: `gen_ai.system` | `ai.model.provider` (normalize
  compound values such as `openai.chat` to the provider segment);
  `gen_ai.request.model` | `ai.model.id`; `gen_ai.response.model` |
  `ai.response.model`.
- Span selection: only call-level spans (`*.doGenerate`, `*.doStream`)
  become attempt events, preventing double counting against root spans; the
  root span id becomes `logical_request_id` correlation via parent span id.
  `ai.toolCall` spans increment `tool_call_count`/`tool_histogram` on the
  parent when present in the same batch; they never become standalone events.
- Dimension mapping: `ai.telemetry.functionId` to `dimensions["function_id"]`,
  `ai.telemetry.metadata.*` through a server-side allowlist into
  `dimensions`, `operation.name`/`ai.operationId` to `outcome`-adjacent
  operation dimension.
- Content stripping extends `CONTENT_ATTRS` with `ai.prompt`,
  `ai.prompt.messages`, `ai.response.text`, `ai.response.object`,
  `ai.toolCall.args`, `ai.toolCall.result` and prefix rules.

`@tokemetry/ai-sdk` package:

- `tokemetryExporter(options)`: a configured OTLP/HTTP span exporter
  (endpoint, bearer header, protobuf default with JSON fallback option) with
  an AI-span filter, usable inside `registerOTel` (Vercel), `NodeSDK`
  (self-hosted), or `@ai-sdk/otel`'s processor chain (v7).
- `withTokemetry(settings?)`: returns a per-call telemetry settings object
  with `isEnabled: true`, `recordInputs: false`, `recordOutputs: false`
  defaults (privacy inversion of SDK defaults), merging user metadata.
- TypeScript, dual ESM/CJS, no runtime dependency on `ai` itself (peer range
  covering v4-v7); Next.js `instrumentation.ts` and plain Node recipes.

### 8.2 Functional requirements

- FR-SDK-001: The OTLP receiver MUST accept OTLP/protobuf and OTLP/JSON on
  the existing traces path, negotiated by Content-Type.
- FR-SDK-002: Attribute normalization MUST recognize the AI SDK dialects in
  Section 8.1 alongside stable `gen_ai.*` semconv, with deterministic
  precedence and unit coverage per dialect.
- FR-SDK-003: Only call-level spans MUST produce attempt events; parent/root
  and tool-call spans MUST NOT double count usage.
- FR-SDK-004: Content attributes across both dialects MUST be stripped
  before persistence; prohibited-key fuzz tests MUST cover the new names.
- FR-SDK-005: Cache and reasoning token mapping MUST be best-effort and
  documented as lossy pending upstream fixes; absence MUST NOT reject spans.
- FR-SDK-006: `ai.telemetry.functionId` and allowlisted
  `ai.telemetry.metadata.*` keys MUST map to event `dimensions`.
- FR-SDK-007: The semconv version and dialect used MUST be recorded per
  event (existing `extra.otel` namespace), satisfying FR-OTEL-006.
- FR-SDK-008: `@tokemetry/ai-sdk` MUST default `recordInputs` and
  `recordOutputs` to false and document the privacy rationale prominently.
- FR-SDK-009: The npm package MUST support AI SDK v4 through v7 via the
  exporter/span-processor pattern and document `registerTelemetry` (v7),
  `registerOTel` (Vercel), and `NodeSDK` (self-hosted) wiring.
- FR-SDK-010: The exporter MUST filter to AI spans by default so tokemetry
  does not receive an application's full trace volume; a passthrough opt-in
  MAY forward everything (server discards non-GenAI spans regardless).
- FR-SDK-011: An end-to-end fixture suite (recorded AI SDK OTLP payloads,
  protobuf and JSON, v5 and v7 shapes) MUST run against the receiver in CI.
- FR-SDK-012: Receiver behavior MUST remain feature-flagged and off by
  default (`otel_receiver_enabled` unchanged).

### 8.3 Task decomposition (Task Master)

1. OTLP/protobuf parsing in the receiver (bindings, content-type
   negotiation, size limits) with fixture tests.
2. Semconv dialect normalization tables and precedence engine plus content
   stripping extension (unit matrix per dialect).
3. Call-level span selection, root-span correlation, and tool-call
   aggregation (double-counting regression tests).
4. Dimension allowlist plumbing and settings.
5. `@tokemetry/ai-sdk` package: exporter, `withTokemetry`, builds, unit
   tests, versioned peer-range CI matrix.
6. End-to-end recorded-payload suite and `docs/integrations/vercel-ai-sdk.md`
   (Next.js, Node, v7 registerTelemetry, privacy defaults, known upstream
   token-accounting gaps).

---

## 9. Epic TOK-15: Provider Billing Reconciliation

### 9.1 Design summary

Scheduled, read-only importers pull provider-reported usage and cost into
new billing tables, keyed for upsert, and reconciliation becomes three-way:
tokemetry-computed (rate cards), observed (per-event `observed_cost`), and
provider-billed (imported lines). Follows the memory-binding multi-provider
rule: abstract interface in `packages/core`, OpenAI and Anthropic
implementations, and a fake importer proving the contract.

Research facts the design relies on:

- OpenAI: `GET /v1/organization/usage/completions` (+ siblings per modality)
  and `GET /v1/organization/costs`, admin key auth. Usage supports
  `bucket_width` 1m/1h/1d, `group_by` project_id/user_id/api_key_id/model/
  batch/service_tier, cached and cache-write token splits, cursor
  pagination. Costs is daily-bucket only, `group_by` project_id/line_item,
  `amount.value`/`currency`. Data can lag tens of minutes; invoices remain
  authoritative (credits, refunds, tax).
- Anthropic: `GET /v1/organizations/usage_report/messages` and
  `GET /v1/organizations/cost_report`, `x-api-key` admin key plus
  `anthropic-version`. Usage groups by api_key_id/workspace_id/model/
  service_tier/context_window with cache splits; cost report is daily,
  groups by workspace_id/description, USD decimal strings; Priority Tier
  spend is excluded from cost_report (track via usage service_tier).
  Data typically appears within about 5 minutes; documented polling
  etiquette is about once per minute sustained.
- Both paginate via `has_more`/`next_page`. Neither offers read-only admin
  keys today (OpenAI admin keys are org-level; Anthropic admin keys are
  org-wide read-write) — keys MUST be treated as high-privilege secrets.
- Future providers (Bedrock, Azure OpenAI, Gemini) expose only daily cost
  lines with coarse grouping through their cloud billing exports; OpenRouter
  has credits/activity endpoints; Z.ai has no public usage API. The
  abstraction therefore bottoms out at day-granularity cost lines without
  per-request detail.
- Prior art (LiteLLM cost-discrepancy guide, Helicone) treats computed cost
  as observability and the provider bill as reconciliation truth; ~10%
  variance is normal rounding/boundary noise; OpenAI folds cache reads into
  input counts while Anthropic reports them separately.

Architecture:

- `packages/core/src/tokemetry_core/billing.py`: `BillingImporter` ABC with
  `capabilities()` (usage granularity, cost granularity, group dimensions),
  `fetch_usage(window, granularity)`, `fetch_costs(window)` yielding
  normalized `BillingUsageLine`/`BillingCostLine` dataclasses; registered on
  the existing `ProviderRegistry`. Implementations: `openai`, `anthropic`,
  `fake` (deterministic fixtures).
- New tables (Alembic `0028+`): `billing_accounts` (provider, label,
  enabled, cadence, lookback_days, last_sync_at, watermark_day,
  consecutive_failures), `billing_cost_lines` (account_id, day, group-key
  JSON with generated hash column for uniqueness, line_item, amount NUMERIC,
  currency, imported_at) upserted on (account_id, day, group_hash), and
  `billing_usage_lines` (token fields mirroring v2 counters where the
  provider reports them). Admin credentials stored via the
  `channel_config`-style DB-over-env pattern, encrypted at rest with a
  server-side key (new `security` helper; key from settings), masked in all
  API responses.
- Sync worker: `_billing_loop` in the lifespan (settings-gated), default
  every 6 hours; each run re-pulls a rolling lookback window (default 7
  days) and upserts, plus a monthly finalization pass re-pulling the prior
  calendar month. Watermark plus lookback makes runs idempotent and
  restart-safe. Per-account failure counters feed a `billing_sync_stale`
  alert kind.
- Reconciliation: `queries_v2` gains billing-aware aggregation joining
  computed costs and billing cost lines by provider/day (and project where
  group keys allow); `GET /api/v2/costs/reconciliation` gains a
  `basis=observed|billed` parameter (default observed, backward
  compatible). Drift beyond a configurable percentage raises a new
  `billing_drift` alert kind. Dashboard Costs view gains a
  computed-vs-billed panel with drift badges.

### 9.2 Functional requirements

- FR-BILL-001: A provider-neutral `BillingImporter` interface MUST live in
  `packages/core` with OpenAI, Anthropic, and fake implementations; server
  code MUST depend only on the interface.
- FR-BILL-002: Importers MUST be read-only against provider APIs and MUST
  respect documented polling etiquette (bounded request rate, pagination).
- FR-BILL-003: Imports MUST be idempotent day-bucket upserts keyed by
  account, day, and normalized group dimensions; re-running any window MUST
  converge.
- FR-BILL-004: Each sync MUST re-fetch a configurable rolling lookback
  window (default 7 days) and a monthly finalization pass MUST re-fetch the
  prior month.
- FR-BILL-005: Admin/provider credentials MUST be stored encrypted at rest,
  masked in every API response, mutable via admin API with DB-over-env
  precedence, and MUST never appear in logs.
- FR-BILL-006: Reconciliation MUST support three bases — computed, observed,
  provider-billed — without changing the default behavior of the existing
  endpoint.
- FR-BILL-007: A `billing_drift` alert kind MUST fire when |computed minus
  billed| exceeds a configurable percentage over a configurable window, and
  a `billing_sync_stale` kind when an enabled account has not synced within
  its cadence times a multiplier.
- FR-BILL-008: Sync status (per account: last sync, watermark, failures)
  MUST be exposed via an admin API endpoint and on the dashboard data
  quality view.
- FR-BILL-009: Token-level usage import MUST be optional per account
  (cost-only accounts supported), since future providers only provide cost
  lines.
- FR-BILL-010: Amounts MUST be stored as exact decimals with currency; no
  float arithmetic in reconciliation paths.
- FR-BILL-011: New scope `admin:billing` MUST gate account management and
  manual sync triggers; `query:read` suffices for reconciliation reads.
- FR-BILL-012: Documentation MUST state that provider invoices remain
  authoritative and that roughly ten percent variance can be normal, citing
  cache-token accounting differences between providers.

### 9.3 Task decomposition (Task Master)

1. Core `BillingImporter` interface, normalized line dataclasses, fake
   importer, registry wiring (unit tests against the fake).
2. Schema and migrations for accounts and cost/usage lines; credential
   encryption helper (migration tests, SQLite and Postgres).
3. OpenAI importer (usage plus costs, pagination, lookback) with recorded
   fixture tests.
4. Anthropic importer (usage_report plus cost_report, service-tier caveat)
   with recorded fixture tests.
5. Sync worker loop, watermarking, finalization pass, failure accounting,
   admin API and scope (integration tests with the fake importer).
6. Three-way reconciliation queries, endpoint parameter, alert kinds, seeds
   (regression tests protecting the existing endpoint contract).
7. Dashboard: billing accounts admin UI, sync status on data quality,
   computed-vs-billed panel on Costs.
8. Docs: `docs/integrations/provider-billing.md` (key provisioning and risk,
   cadence, drift interpretation) and API docs updates.

---

## 10. Epic TOK-16: Generic Outbound Webhooks with Slack and Teams Presets

### 10.1 Design summary

A signed, reliable webhook fan-out for alert events, implemented as a
first-class endpoint registry (not another singleton channel), plus payload
presets for Slack and Microsoft Teams so no per-tool transport code is
needed.

Research facts the design relies on:

- Standard Webhooks spec: HMAC-SHA256 over `{id}.{timestamp}.{payload}`,
  headers `webhook-id`, `webhook-timestamp`, `webhook-signature` (space-
  delimited list enabling dual-signing rotation), `v1,`-prefixed base64
  signatures, `whsec_` secrets, about 5-minute receiver timestamp
  tolerance; envelope convention `{type, timestamp, data}`; Python
  reference library `standardwebhooks` exists.
- Reliability conventions (Svix/Stripe): retry schedule from seconds to
  hours across roughly 24h, about 15s per-attempt timeout, only 2xx is
  success, redirects are failures, at-least-once delivery, per-endpoint
  delivery log with status/latency and manual resend, auto-disable after
  sustained failure with owner notification.
- SSRF guidance (OWASP): block loopback, RFC1918, link-local (including
  169.254.169.254), and multicast ranges; validate the resolved IP at
  connection time and pin it (DNS rebinding); scheme allowlist; no
  automatic redirect following.
- Slack: app-based incoming webhooks remain supported; Block Kit bodies;
  1 message/second per webhook with 429/Retry-After.
- Teams: Office 365 connectors are disabled during May 2026; the current
  target is Workflows (Power Automate) webhook URLs accepting Adaptive
  Cards wrapped in an `attachments` array with content type
  `application/vnd.microsoft.card.adaptive`. MessageCard is legacy;
  interactive rendering is not supported under Workflows.
- Templating prior art (Grafana custom payload, Uptime Kuma): a sandboxed
  template over an allowlisted variable context, never arbitrary code.

Architecture:

- New tables: `webhook_endpoints` (id, name, url, preset generic|slack|teams,
  secret_encrypted, previous_secret_encrypted plus rotation deadline,
  enabled, event-kind filter JSON, min severity, extra headers JSON,
  template TEXT nullable, disabled_reason, consecutive_failures) and
  `webhook_deliveries` (endpoint_id, alert_event_id, webhook_id UUID,
  attempt, status pending|success|failed|dropped, http_status, latency_ms,
  error, created_at, next_retry_at), with retention wired into the existing
  retention worker (default 90 days).
- Delivery service `services/alerting/webhooks.py`: async queue consumer
  using `app.state.http_client` with per-request overrides: 15s timeout, no
  redirects, connect-time IP validation and pinning (custom httpx transport
  hook), HTTPS required by default with an explicit
  `webhook_allow_private_targets` escape hatch for LAN/WireGuard
  deployments (defaults false; both toggles documented as security
  decisions). Retry schedule configurable, default 5s, 5m, 30m, 2h, 5h;
  after the final failure the delivery is marked failed; after N
  consecutive fully-failed deliveries (default 20) the endpoint auto-
  disables and a `webhook_endpoint_disabled` alert fires through remaining
  channels.
- Signing per Standard Webhooks including dual-signature rotation windows;
  generic preset sends the canonical envelope `{type: "tokemetry.alert.<kind>",
  timestamp, data: {rule, severity, title, body, dimensions, links}}`.
- Presets transform the same alert context into Block Kit (Slack) or an
  Adaptive Card in `attachments` (Teams); Slack deliveries are paced to
  1/sec per endpoint and honor Retry-After. Preset endpoints skip Standard
  Webhooks headers (Slack/Teams URLs are capability URLs).
- Optional custom template (generic preset only): `string.Template`
  substitution over an allowlisted variable set (no expression language, no
  attribute traversal), validated at save time.
- `AlertEngine` gains a webhook dispatch alongside `Notifier` fan-out using
  the same reconfigure-on-save pattern; endpoint CRUD, secret rotation,
  test-delivery, and delivery-log endpoints under `/api/alerts/webhooks`,
  gated by a new `admin:alerts` scope (existing alert admin endpoints adopt
  it in the same change, with migration notes). Dashboard: a Webhooks card
  on the Alerts page (list, create with secret-shown-once, filters, test
  button, delivery log with status and latency, resend).

### 10.2 Functional requirements

- FR-WH-001: Generic deliveries MUST implement Standard Webhooks signing
  (headers, `v1,` prefix, `whsec_` secret format) verifiable by the
  `standardwebhooks` reference library in tests.
- FR-WH-002: Secrets MUST be generated server-side, shown once, stored
  encrypted, and support dual-signing rotation with a configurable overlap
  (default 24h).
- FR-WH-003: Delivery MUST be at-least-once with the configurable retry
  schedule, 15s default timeout, 2xx-only success, and no redirect
  following.
- FR-WH-004: SSRF controls MUST validate scheme (https default) and the
  connect-time resolved IP against loopback, private, link-local, and
  multicast ranges, with IP pinning; the private-target escape hatch MUST
  default to false.
- FR-WH-005: Every delivery attempt MUST be recorded with status, HTTP
  code, and latency; a per-endpoint log MUST be queryable and support
  manual resend.
- FR-WH-006: Endpoints MUST filter by alert kind and minimum severity, and
  MUST auto-disable after sustained failure with a notification through
  remaining channels.
- FR-WH-007: Slack preset MUST emit Block Kit payloads, pace to one message
  per second per endpoint, and honor Retry-After on 429.
- FR-WH-008: Teams preset MUST emit Adaptive Cards in the `attachments`
  envelope compatible with Workflows webhook URLs; MessageCard MUST NOT be
  used.
- FR-WH-009: Custom templates MUST be sandboxed substitution over an
  allowlisted variable context, validated at save; template errors at send
  time fall back to the canonical envelope and are logged on the delivery.
- FR-WH-010: Alert context sent to webhooks MUST remain content-free
  (FR-ALERT-010 applies unchanged).
- FR-WH-011: Test delivery MUST be available per endpoint and reuse the
  full signing/delivery path against a synthetic event.
- FR-WH-012: Webhook management MUST require the new `admin:alerts` scope;
  delivery logs MUST redact secrets and full URLs to a display-safe form.

### 10.3 Task decomposition (Task Master)

1. Schema and migrations for endpoints and deliveries plus retention
   wiring; secret encryption and rotation model.
2. Delivery service: signing, retry queue, SSRF-guarded transport, failure
   accounting, auto-disable (unit tests with a fake transport; signature
   verification via the reference library).
3. Alert engine integration and reconfigure path; `admin:alerts` scope
   adoption.
4. Slack and Teams presets plus pacing (payload snapshot tests).
5. Management API: CRUD, rotation, test-delivery, delivery log, resend.
6. Dashboard Webhooks card and delivery log UI.
7. Docs: `docs/alerting.md` extension plus `docs/integrations/webhooks.md`
   (receiver verification examples, Slack and Teams setup with the 2026
   Workflows migration context, security model).

---

## 11. Epic TOK-17: Prometheus Metrics Endpoint

### 11.1 Design summary

A `/metrics` exposition endpoint for server health so tokemetry fits
existing SRE monitoring without granting Grafana database access. Grafana
SQL views remain the analytics path; `/metrics` is operational only.

Research facts the design relies on:

- `prometheus_client` exposes ASGI apps and `generate_latest`; content
  negotiation serves OpenMetrics 1.0 or classic text. Custom collectors
  (`collect()` at scrape time) are the sanctioned pattern for DB-derived
  gauges; exporters should not run their own scrape timers.
- Multiprocess mode requires `PROMETHEUS_MULTIPROC_DIR`, loses Info/Enum
  and custom collectors, and needs worker-death hooks. Tokemetry deploys
  single-worker uvicorn today, so the design targets a single process and
  documents the constraint; multi-worker metrics support is explicitly out
  of scope until the deployment model changes.
- Naming conventions: base units, `_total` counters, timestamps as
  `*_timestamp_seconds` gauges so alerts compute `time() - metric`;
  avoid high-cardinality labels.
- `prometheus-fastapi-instrumentator` 8.x is maintained (8.0.2, 2026-06)
  and provides default HTTP metrics; multiprocess wiring is undocumented
  there — acceptable under the single-process decision.
- Exporters conventionally ship unauthenticated and rely on network
  isolation; Prometheus scrape configs support bearer_token_file when auth
  is wanted.

Metric inventory (prefix `tokemetry_`):

- `ingest_events_accepted_total{api_version, source_type}`,
  `ingest_events_rejected_total{api_version, reason}`,
  `ingest_batches_total{api_version}` — in-process counters at the ingest
  services.
- `source_last_ingest_timestamp_seconds{source_type, source_name}` and
  `source_stale{...}` (0/1 against the per-type threshold) — custom
  collector over the `sources` table; cardinality is bounded by the
  registered source count.
- `backup_last_success_timestamp_seconds` — custom collector reading the
  backup marker (`deploy/backup.sh` gains a machine-readable marker file or
  row; decision D-104).
- `retention_last_run_timestamp_seconds`, `retention_backlog_rows{table}` —
  custom collector over `retention_status`.
- `alerts_fired_total{kind, severity}`, `webhook_deliveries_total{status}`
  (once TOK-16 lands), `billing_sync_last_success_timestamp_seconds{provider}`
  (once TOK-15 lands).
- `rate_limit_throttled_total{traffic_class}` for ingest/query limiter
  saturation.
- Default HTTP metrics via instrumentator (request counts, durations,
  sizes), with route templating to avoid path-cardinality explosions.

Endpoint: `GET /metrics` on the main app, enabled by
`TOKEMETRY_METRICS_ENABLED` (default false), exempt from bearer auth like
health paths (`api/security.py` allowlist) but optionally protected by a
static `TOKEMETRY_METRICS_BEARER_TOKEN` compared constant-time; deployment
docs emphasize binding/firewalling (WireGuard) as the primary control. Repo
ships `deploy/prometheus/` with an example scrape config, alert rules
(staleness via `time() -` patterns), and a starter Grafana dashboard JSON.

### 11.2 Functional requirements

- FR-MET-001: `GET /metrics` MUST serve classic text and OpenMetrics via
  content negotiation, gated by a settings flag defaulting off.
- FR-MET-002: Metric and label names MUST follow Prometheus conventions
  (base units, `_total`, `*_timestamp_seconds`); a naming lint test MUST
  enforce the prefix and suffix rules.
- FR-MET-003: DB-derived gauges MUST be computed at scrape time by custom
  collectors with per-query timeouts; a scrape MUST complete within 10s on
  the reference dataset.
- FR-MET-004: Ingest accept/reject counters MUST be incremented in-process
  at the v1 and v2 ingest paths without measurable ingest latency impact
  (perf test guard).
- FR-MET-005: Label cardinality MUST be bounded: sources by registry count,
  reasons and kinds by enum; no free-form label values.
- FR-MET-006: The endpoint MUST be auth-exempt by default with an optional
  static bearer check; when the bearer is configured, unauthenticated
  scrapes MUST get 401 without body detail.
- FR-MET-007: Single-process operation MUST be documented as a constraint;
  enabling metrics with multiple workers MUST log a startup warning naming
  the limitation.
- FR-MET-008: `/metrics` output MUST never include token values, URLs with
  secrets, or event metadata values (label sources are registry names and
  enums only).
- FR-MET-009: The repo MUST ship example scrape config, alert rules, and a
  Grafana dashboard under `deploy/prometheus/`, referenced from operations
  docs.
- FR-MET-010: Metrics registration MUST degrade gracefully: TOK-15/TOK-16
  series appear only when those subsystems are enabled.

### 11.3 Task decomposition (Task Master)

1. Endpoint, settings, auth exemption plus optional bearer, content
   negotiation (unit and security tests).
2. Ingest and rate-limit counters instrumentation (both API versions).
3. Custom collectors for sources, retention, backup marker (D-104 marker
   change in `deploy/backup.sh` with shellcheck), alerts.
4. HTTP metrics via instrumentator with route templating and cardinality
   tests.
5. `deploy/prometheus/` examples plus `docs/operations/metrics.md`.

---

## 12. Epic TOK-18: OIDC Authentication and RBAC

### 12.1 Design summary

Humans authenticate to the dashboard via OIDC; machines keep scoped bearer
tokens unchanged. The FastAPI server acts as the OAuth client (BFF): the
SPA never holds tokens and receives only an HttpOnly session cookie.

Research facts the design relies on:

- RFC 9700 (OAuth 2.0 Security BCP) mandates Authorization Code + PKCE and
  deprecates implicit and password grants; the IETF browser-based apps
  draft recommends the BFF pattern for SPAs.
- Authlib (BSD-3, 1.7.x, actively maintained) is the only mainstream Python
  library with full OIDC client support — discovery, code exchange with
  PKCE, refresh, RP-initiated logout — integrated with Starlette/FastAPI.
- Only `sub` is guaranteed in ID tokens; `email`/`preferred_username`
  require scopes and provider support. Group/role claims are non-standard:
  Keycloak and Authentik use admin-configured claim names, Google requires
  a Directory API call, Entra caps groups at 200 with an overage claim.
  Therefore: configurable groups-claim name, group-to-role mapping, default
  role fallback, `sub` as the durable identity key.
- Cookie hardening: `__Host-` prefix, HttpOnly, Secure, SameSite=Lax;
  OWASP recommends signed double-submit CSRF tokens for cookie-authed APIs.
- Prior-art role taxonomies (Grafana, Langfuse, LiteLLM) support a minimal
  admin/viewer global pair with a per-project scoping layer as the smallest
  useful model.
- Rollout convention: additive SSO behind an auth-mode flag with bootstrap
  admin mapping, existing credentials untouched.

Architecture:

- New module `apps/server/src/tokemetry_server/auth/` (OIDC client via
  Authlib, session store, CSRF). New tables: `users` (id, sub, issuer,
  email, display_name, role admin|viewer, disabled, created_at,
  last_login_at), `user_project_scopes` (user_id, project pattern),
  `auth_sessions` (id, user_id, created_at, expires_at, revoked) — server-
  side sessions so logout and revocation are real.
- Settings: `TOKEMETRY_AUTH_MODE` = `token` (default, current behavior) |
  `both` | `oidc`; `TOKEMETRY_OIDC_ISSUER` (discovery),
  `TOKEMETRY_OIDC_CLIENT_ID/SECRET`, `TOKEMETRY_OIDC_SCOPES`,
  `TOKEMETRY_OIDC_GROUPS_CLAIM`, `TOKEMETRY_OIDC_GROUP_ROLE_MAP`,
  `TOKEMETRY_OIDC_BOOTSTRAP_ADMIN` (email or group; first matching login
  becomes admin), cookie/session lifetimes.
- Endpoints: `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`
  (RP-initiated logout redirect), `GET /auth/me`. Session cookie
  `__Host-tokemetry_session`, HttpOnly, Secure, SameSite=Lax; CSRF token
  endpoint plus double-submit check on state-changing cookie-authed calls.
- Authorization layering: `Principal` generalizes to token-principals
  (unchanged) and user-principals (role plus project scopes). `query:read`-
  equivalent access for viewers is filtered by project scope at the query
  layer; admin-only routes require role admin or an admin-scoped token.
  Machine ingest and existing API clients see zero change in every mode.
- Dashboard: login view and session mode in `api/client.ts` (cookie-based,
  no bearer) alongside the existing token gate, selected by a
  `GET /auth/config` capability probe; logout and current-user affordances.

### 12.2 Functional requirements

- FR-OIDC-001: Authorization Code + PKCE via server-side client (BFF);
  tokens MUST never reach browser JavaScript.
- FR-OIDC-002: Provider integration MUST be discovery-driven and provider-
  agnostic (verified against Keycloak and Authentik in integration tests,
  with documented recipes for Google and Entra).
- FR-OIDC-003: Identity MUST key on `(issuer, sub)`; missing email or
  username claims MUST NOT break login.
- FR-OIDC-004: Group-to-role mapping MUST use a configurable claim name and
  mapping table with a default role for unmatched users; the bootstrap
  admin rule MUST prevent lockout.
- FR-OIDC-005: Sessions MUST be server-side, revocable, and bounded;
  cookies MUST use `__Host-`, HttpOnly, Secure, SameSite=Lax; state-
  changing cookie-authed endpoints MUST enforce CSRF.
- FR-OIDC-006: RP-initiated logout MUST clear the local session and
  redirect to the IdP end-session endpoint when the IdP advertises one.
- FR-OIDC-007: `AUTH_MODE` MUST default to `token` with byte-identical
  current behavior; `both` adds OIDC; `oidc` disables the dashboard token
  gate but MUST NOT affect bearer-token API access for machines.
- FR-OIDC-008: Roles MUST be admin and viewer initially; viewers MUST be
  restrictable to project patterns enforced in the query layer (tested with
  scoped fixtures).
- FR-OIDC-009: Admin surfaces (tokens, pricing, retention, alerts,
  webhooks, billing accounts, budgets) MUST require admin role or the
  corresponding admin token scope.
- FR-OIDC-010: Auth events (login, logout, denied, bootstrap promotion)
  MUST be audit-logged without tokens or claims payloads.
- FR-OIDC-011: Security tests MUST cover CSRF, cookie flags, open-redirect
  hardening on callback, session fixation, and mode-matrix regression
  (token clients across all three modes).
- FR-OIDC-012: Authlib MUST be the OIDC client dependency; version pinned
  and license-noted.

### 12.3 Task decomposition (Task Master)

1. Schema and migrations (users, sessions, project scopes); settings and
   auth-mode plumbing.
2. OIDC client flow (login, callback, PKCE, discovery), session issuance.
3. Session middleware, CSRF, cookie hardening, logout.
4. Principal generalization, role checks, viewer project-scope query
   filtering.
5. Group-claim mapping and bootstrap admin.
6. Dashboard login/session mode, capability probe, user menu.
7. Integration tests against a containerized IdP (Keycloak) plus security
   suite; `docs/deployment/oidc.md` with per-IdP recipes.

---

## 13. Epic TOK-19: Budgets and Forecast Alerts

### 13.1 Design summary

Budgets attach to a scope (global, provider, project, or source), a period
(calendar day/week/month in a configured timezone, or rolling window), and
an amount (USD, subscription-equivalent USD, or tokens). Evaluation reuses
the alert engine; forecasting starts with linear burn-rate extrapolation
(`spend_so_far / elapsed * total_period`), the same method hyperscaler
budget alerts use, with a minimum data threshold (default 3 days for
monthly periods) before forecast alerts can fire. Prior art: LiteLLM
budgets with duration resets and threshold alerting; AWS/OCI forecasted-
to-exceed alerts.

Architecture:

- Table `budgets` (id, name, scope_type, scope_value, metric usd|
  subscription_usd|tokens, amount NUMERIC, period day|week|month|rolling,
  rolling_days, timezone, thresholds JSON default [0.8, 1.0], forecast
  enabled, owner, enabled, created_at). Ownership metadata: free-text
  `owner` plus optional link, shown in alerts and reports.
- New evaluator kinds in the existing rule registry: `budget_pct`
  (threshold crossings, deduplicated per period by the engine's grouped
  state) and `budget_forecast` (linear extrapolation exceeds amount before
  period end). Spend resolution uses computed costs (existing rollups/
  queries), falling back to billed lines (TOK-15) when configured basis
  says so.
- API `GET/POST/PUT/DELETE /api/v2/budgets` plus `GET /api/v2/budgets/{id}/status`
  (spend, burn rate, forecast, threshold states), scope `admin:budgets` for
  writes. Dashboard: Budgets management on the Costs area with status
  bars, forecast badge, and owner column; alert rules auto-created per
  budget (visible and editable in the Alerts UI).

### 13.2 Functional requirements

- FR-BUD-001: Budgets MUST support the four scope types and three metrics
  with exact-decimal accounting.
- FR-BUD-002: Periods MUST support calendar day/week/month in a configured
  IANA timezone plus rolling N-day windows; period boundaries MUST be
  timezone-correct (unit-tested across DST).
- FR-BUD-003: Threshold alerts MUST fire once per threshold per period per
  budget (engine cooldown/grouping), through all configured channels
  including webhooks.
- FR-BUD-004: Forecast alerts MUST use linear burn-rate extrapolation with
  a configurable minimum-elapsed guard and MUST state the method and inputs
  in the alert body.
- FR-BUD-005: Budget evaluation MUST run inside the existing alert loop
  without a new scheduler and MUST be resilient to missing data (no spend
  yet: no alert, status reports zero).
- FR-BUD-006: Budget status MUST be queryable per budget and in aggregate
  for dashboard rendering.
- FR-BUD-007: Writes MUST require `admin:budgets` (or admin role);
  read access follows viewer project scoping (a viewer sees only budgets
  whose scope intersects their projects).
- FR-BUD-008: Deleting or disabling a budget MUST retire its alert state
  cleanly (no orphaned cooldowns).
- FR-BUD-009: Docs MUST cover budget semantics, forecast interpretation,
  and interaction with subscription-equivalent value metrics.

### 13.3 Task decomposition (Task Master)

1. Schema, migration, CRUD API, scope enforcement.
2. Spend resolution service over existing rollups (basis selection,
   timezone periods) with heavy unit coverage.
3. Evaluator kinds, engine wiring, per-period dedupe, retirement.
4. Dashboard budgets UI and status rendering.
5. Docs and API reference updates.

---

## 14. Cross-Cutting Requirements

- FR-X-001: New scopes (`admin:alerts`, `admin:billing`, `admin:budgets`)
  MUST be added to `scopes.py`, validated, documented, and covered by
  scope-matrix tests; existing tokens keep working (additive only).
- FR-X-002: Every new endpoint MUST appear in OpenAPI; generated Python and
  TypeScript clients (`packages/clients/`) MUST be regenerated in the same
  epic that adds the endpoint.
- FR-X-003: Every new subsystem MUST honor the settings conventions
  (`TOKEMETRY_` prefix, feature flag defaulting off until its epic's final
  task flips documentation defaults).
- FR-X-004: Privacy invariants (FR-PRIV-001..012) apply to all new ingest
  and outbound paths; prohibited-key fuzzing MUST extend to LiteLLM
  payload mapping, AI SDK attribute stripping, and webhook payloads.
- FR-X-005: All schema changes ship Alembic migrations with SQLite and
  Postgres migration tests, continuing the `00NN_name.py` sequence.
- FR-X-006: Each epic updates `docs/` in the same tasks that add the
  capability (workflow step 4), including `docs/api/` references and the
  operations runbook where operational surface changes.
- FR-X-007: New Python dependencies (Authlib, prometheus-client,
  prometheus-fastapi-instrumentator, standardwebhooks, opentelemetry-proto)
  and npm dependencies MUST pass trivy and license review (all are
  MIT/BSD/Apache per current research).
- FR-X-008: Exporter packages (`tokemetry-litellm`, `@tokemetry/ai-sdk`)
  MUST have their own CI, semver, changelogs, and publishing workflows, and
  MUST NOT depend on server internals — wire-format contract only, pinned
  to the published JSON schema (`GET /api/v2/schemas/usage-event`).

---

## 15. Non-Functional Requirements

- NFR-101: Ingest hot paths gain no measurable latency from metrics
  counters or new validation branches (existing perf tests extended).
- NFR-102: Webhook and billing workers MUST not block the event loop;
  all outbound I/O is async with bounded concurrency.
- NFR-103: A `/metrics` scrape completes in under 10 seconds on the
  reference dataset (FR-MET-003).
- NFR-104: All new tables carry indexes supporting their dashboard queries;
  reconciliation joins are covered by query tests with EXPLAIN budget
  assertions where the suite already does so.
- NFR-105: Quality gates unchanged and binding: pytest 100% pass with
  coverage floors (80% line / 70% branch), ruff and shellcheck zero
  warnings, mypy strict clean, trivy zero HIGH/CRITICAL (scanned from repo
  root), no hardcoded secrets.

---

## 16. Risks and Mitigations

- R-011 **LiteLLM schema drift.** StandardLoggingPayload is extended
  weekly and has documented doc/source gaps. Mitigation: tolerant mapper
  (unknown fields ignored), pinned-minimum plus latest CI matrix, schema-
  drift alert kind already exists server-side.
- R-012 **AI SDK telemetry instability.** v7 restructured telemetry and
  upstream token-attribute bugs are open. Mitigation: dialect table driven
  by fixtures, best-effort cache/reasoning fields, adapter pattern that
  avoids SDK-internal registries.
- R-013 **High-privilege billing keys.** Neither OpenAI nor Anthropic
  offers read-only admin keys. Mitigation: encrypted at rest, masked
  everywhere, dedicated importer code path, explicit key-risk
  documentation, no key material in logs or metrics.
- R-014 **Webhook SSRF and secret leakage.** Mitigation: FR-WH-004
  controls, capability-URL redaction in logs, security test suite.
- R-015 **Metrics cardinality explosion.** Mitigation: enum/registry-bound
  labels only, cardinality tests, route templating for HTTP metrics.
- R-016 **Auth regression locking out machines.** Mitigation: mode-matrix
  regression tests (FR-OIDC-011) and additive rollout with `token` default.
- R-017 **Forecast false positives.** Linear extrapolation early in a
  period overshoots. Mitigation: minimum-elapsed guard, method disclosure
  in alert bodies, thresholds configurable per budget.
- R-018 **Scope creep toward SaaS multi-tenancy.** Mitigation: NG-104
  boundary; project-scope RBAC only.

---

## 17. Resolved Decisions

- D-101: LiteLLM `response_cost` maps to `observed_cost` with gateway
  provenance; it never replaces tokemetry-computed cost.
- D-102: `@tokemetry/ai-sdk` uses the exporter/span-processor pattern, not
  the v7-internal telemetry registry, to cover AI SDK v4-v7 with one
  package.
- D-103: Billing imports are day-bucket upserts with rolling lookback
  (default 7 days) plus monthly finalization; per-request billing detail is
  out of scope for importers.
- D-104: `deploy/backup.sh` writes a machine-readable success marker
  (timestamp) that the metrics collector reads; the metrics epic owns this
  change including shellcheck.
- D-105: Webhook endpoints are a registry (N endpoints with filters), not a
  fourth singleton channel; Slack/Teams are payload presets over the same
  delivery service.
- D-106: `/metrics` targets single-process deployments; multiprocess
  support is deferred until the deployment model requires it (startup
  warning otherwise).
- D-107: OIDC uses the BFF pattern with Authlib and server-side sessions;
  the SPA never holds OAuth tokens.
- D-108: RBAC starts with admin/viewer plus viewer project scoping;
  team/org hierarchies are out of scope.
- D-109: Budget forecasting is linear burn-rate extrapolation in v1.
- D-110: Epic order is TOK-13, TOK-14, TOK-15, TOK-16 and TOK-17 in
  parallel, TOK-18, TOK-19.

---

## 18. Task Master Decomposition Guidance

1. Create one parent task per epic (TOK-13 through TOK-19), appended to the
   `master` tag after the existing parent tasks (60-71 belong to
   PRD-TOK-002).
2. Use the per-epic "Task decomposition" lists as the subtask skeletons;
   every subtask carries verbose implementation detail, references its
   FR IDs, and is sized to one full quality-gated workflow unit (implement,
   document, test, lint, type-check, scan, review, commit).
3. Keep schema/migration, service, API, exporter-package, dashboard, and
   docs work in separate subtasks; never combine dashboard work with server
   core changes.
4. Cross-cutting requirements (Section 14) attach as acceptance criteria to
   the relevant subtasks, not as separate tasks, except client regeneration
   (FR-X-002) which is one subtask per endpoint-adding epic.
5. Epics TOK-16 and TOK-17 have no interdependency and may be scheduled in
   parallel; TOK-19 depends on TOK-18 (admin role) and optionally TOK-15
   (billed basis); TOK-14's npm package depends on its server-side
   normalization subtasks.
6. Preserve requirement IDs (FR-LLM/SDK/BILL/WH/MET/OIDC/BUD/X) in
   generated tasks and in commit messages/PRs.
7. Migration tests accompany every database-changing subtask; privacy fuzz
   tests accompany every ingest-path or outbound-payload subtask.

---

## 19. Reference Sources

Verified July 2026. Implementation must re-verify at build time.

LiteLLM: docs.litellm.ai/docs/observability/custom_callback,
docs.litellm.ai/docs/proxy/logging_spec, docs.litellm.ai/docs/proxy/logging,
github.com/BerriAI/litellm (custom_logger.py, custom_batch_logger.py,
litellm_logging.py, types/utils.py, streaming_handler.py),
docs.litellm.ai/docs/troubleshoot/cost_discrepancy.

Vercel AI SDK: ai-sdk.dev/docs/ai-sdk-core/telemetry,
ai-sdk.dev/docs/migration-guides (5-0, 7-0), github.com/vercel/ai issues
2590, 8426, 8794, 12801, github.com/vercel/otel, nextjs.org OTel guide,
langfuse.com/changelog/2026-06-26-vercel-ai-sdk-7, Braintrust and LangSmith
integration docs.

Provider billing: platform.openai.com/docs/api-reference/usage and /costs,
platform.claude.com/docs/en/manage-claude/usage-cost-api,
platform.claude.com/docs/en/manage-claude/admin-api, cloud billing docs for
Bedrock/Azure/GCP, openrouter.ai/docs.

Webhooks: github.com/standard-webhooks/standard-webhooks (spec),
pypi.org/project/standardwebhooks, docs.svix.com/retries,
docs.stripe.com/webhooks, OWASP SSRF Prevention Cheat Sheet,
docs.slack.dev incoming webhooks and rate limits,
devblogs.microsoft.com/microsoft365dev retirement of Office 365 connectors,
learn.microsoft.com Teams Workflows incoming webhook.

Prometheus: prometheus.github.io/client_python (instrumenting,
multiprocess, custom collectors), prometheus.io/docs/practices/naming,
prometheus.io/docs/instrumenting/writing_exporters,
pypi.org/project/prometheus-fastapi-instrumentator, prometheus.io Prometheus
3.x release notes and native histogram docs.

OIDC/RBAC/budgets: RFC 9700, draft-ietf-oauth-browser-based-apps,
openid.net specs (Core, Discovery, RP-Initiated Logout), docs.authlib.org,
OWASP CSRF Prevention Cheat Sheet, MDN cookie docs, Grafana roles and
service accounts docs, langfuse.com/docs/administration/rbac,
docs.litellm.ai/docs/proxy/access_control and budget docs, Entra optional
claims docs, OCI/AWS budget forecast alert docs.

---

## 20. Definition of Done

PRD-TOK-002 Section 25 applies unchanged to every requirement in this
document. Additionally, for exporter packages: published artifact
(PyPI/npm) installable by version, conformance suite green against a
released tokemetry server, and integration docs verified by following them
verbatim on a clean environment.
