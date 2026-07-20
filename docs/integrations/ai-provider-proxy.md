# AI provider proxy integration contract

The shared wire contract for a gateway/proxy exporter (e.g. RelayPlane) that
transforms its `CanonicalUsageEvent` into tokemetry v2 usage events and submits
them to `POST /api/v2/ingest/events`. Tokemetry owns the wire contract; the
proxy owns the transform. This document is the source of truth for that
transform (companion PRD sections 11.13/11.14, FR-TOK-003..029).

The authoritative machine-readable schema is served at
`GET /api/v2/schemas/usage-event` and in `/openapi.json`; this document explains
the mapping, identity, and privacy semantics that the schema alone cannot.

## Transport

- **Endpoint**: `POST /api/v2/ingest/events`, bearer token, scope `ingest:events`
  (mint an ingest-only token; see the provisioning runbook).
- **Envelope**: `{ "schema_version": 2, "events": [ ...UsageEventV2... ] }`.
  `Content-Encoding: gzip` is accepted (FR-INGEST-010).
- **Batching** (FR-TOK-008..010): default **100 events** and **256 KiB**
  uncompressed per batch; never exceed the server limits (1000 events, 5 MiB).
  Split oversize batches; never split a single event.

## Field mapping (canonical camelCase -> wire snake_case)

Every wire field is snake_case. Unlisted canonical fields go to `extra` under a
namespaced key (see [Extension metadata](#extension-metadata)).

| Canonical (camelCase) | Wire (snake_case) | Notes |
|---|---|---|
| `eventId` | `event_id` | Required, unique in the provider namespace (see [Identity](#event-identity)). |
| `eventKind` | `event_kind` | `attempt` \| `logical_request` \| `import` \| `adjustment`. Proxy attempts are `attempt`. |
| `finality` | `finality` | `snapshot` \| `final` (see [Finality](#finality-and-sequence)). |
| `sequence` | `sequence` | Monotonic per `event_id`; snapshots increase it, the final wins. |
| `logicalRequestId` | `logical_request_id` | Groups a request's attempts; stable across fallbacks. |
| `attemptId` | `attempt_id` | This attempt's id within the request. |
| `providerRequestId` | `provider_request_id` | Provider's own request id, when exposed. |
| `providerResponseId` | `provider_response_id` | Provider's own response id. |
| `provider` | `provider` | Canonical lowercase id (`anthropic`, `openai`, `zai`, ...). |
| `nativeModel` | `native_model` | The provider's own model id actually served. |
| `requestedModel` | `requested_model` | What the caller asked for (visible in trace views). |
| `routedModel` | `routed_model` | What routing selected, if different. |
| `tsStarted` | `ts_started` | Required, RFC 3339, tz-aware (FR-EVENT-013). |
| `tsFirstToken` | `ts_first_token` | First streamed token, tz-aware. |
| `tsCompleted` | `ts_completed` | Terminal timestamp, tz-aware. |
| `inputTokens` | `input_tokens` | Six counters; all non-negative, default 0. |
| `outputTokens` | `output_tokens` | Visible output only. |
| `cacheReadTokens` | `cache_read_tokens` | |
| `cacheWriteShortTokens` | `cache_write_short_tokens` | |
| `cacheWriteLongTokens` | `cache_write_long_tokens` | |
| `reasoningTokens` | `reasoning_tokens` | Kept apart from output (FR-EVENT-015). |
| `success` | `success` | Did the attempt succeed (boolean). |
| `outcome` | `outcome` | Nuanced terminal state (see [Outcome](#outcome-vocabulary)). |
| `httpStatus` | `http_status` | Upstream HTTP status. |
| `stopReason` | `stop_reason` | Provider stop/finish reason. |
| `serviceTier` | `service_tier` | Provider service tier, when reported. |
| `streaming` | `streaming` | Whether the call streamed. |
| `latencyMs` | `latency_ms` | End-to-end latency, non-negative. |
| `timeToFirstTokenMs` | `time_to_first_token_ms` | TTFT, non-negative. |
| `toolCallCount` | `tool_call_count` | Count only, never arguments. |
| `toolHistogram` | `tool_histogram` | `{tool_name: count}`; names only if enabled server-side. |
| `sessionId` / `agentId` / `project` / `environment` / `machine` | same, snake_case | Optional dimensions. |
| `routing` | `routing` | Object, see [Routing](#routing). |
| `provenance` | `provenance` | `official` when read from the provider; else `local_estimate`. |
| `observedCost` | `observed_cost` | Optional upstream cost the proxy observed, for drift reconciliation only (see [Cost reconciliation](#cost-reconciliation)). Non-negative. |
| `source` | `source` | Required source identity, see [Source](#source-and-freshness). |
| `traceId` / `spanId` / `parentSpanId` | `trace_id` / `span_id` / `parent_span_id` | OTel linkage. |

### Routing

`routing` (all optional): `policy`, `reason`, `attempt_index` (0-based),
`fallback_from` (the model this attempt fell back from), `fallback_trigger`.
An **account label** is exported only under `routing` /`dimensions` when the
proxy has account export enabled, and **never** carries secret material
(companion FR-AUTH-008): export a stable label, not the API key or token.

### Extension metadata

- Unknown non-token billable units go to `billable_units` (`{unit: amount}`),
  never token counters.
- Unknown token-like counters and any gateway-specific detail go to `extra`
  under a **provider-namespaced key** (companion FR-USAGE-008 -> FR-EVENT-016),
  e.g. `extra: { "relayplane.usage": { ... } }`. Never invent top-level fields.
- Free-form string dimensions go to `dimensions` (`{key: value}`), subject to
  the server's dimension allowlist.

## Event identity

- Prefer **provider-native ids** for `event_id` / `provider_request_id`
  (companion FR-USAGE-003).
- When the provider exposes none, derive a **deterministic fallback id**
  (companion FR-USAGE-004): a stable hash over `(provider, logical_request_id,
  attempt_index, ts_started)` so a retried export produces the *same* id and the
  server dedupes it rather than double-counting.
- `event_id` is unique **within the provider namespace** (FR-EVENT-002).

## Finality and sequence

- A streamed response emits **snapshots** (`finality: snapshot`) with the same
  `event_id` and a strictly increasing `sequence`, then one **final**
  (`finality: final`) event. The final supersedes all snapshots (FR-EVENT-012,
  FR-IDEMP-004/005).
- Ingest keeps only the highest-`sequence` / final revision of an id, so
  re-sending snapshots is safe and never double counts. Only `attempt` events
  are billable (FR-EVENT-004); `logical_request` summaries are display-only.

## Outcome vocabulary

`success` is the boolean; `outcome` is the nuanced terminal state, one of:
`ok`, `error`, `timeout`, `cancelled`, `rate_limited`, `content_filtered`,
`fallback`. A rate-limited or errored attempt is still ingestible with zero
usage (FR-EVENT-024) so failure rates and fallback chains are visible.

## Source and freshness

`source` is required: `{ type: "gateway", name, version, instance_id? }`. The
server tracks each source's health (last successful ingest, error count, schema
version drift, clock skew), so keep `version` accurate — it drives the source
freshness view. Distinct source identities never merge with collector streams.

## Cost reconciliation

If the proxy observed an upstream cost, submit it in the `observed_cost` field
as **reconciliation metadata** only (companion FR-COST-003/004, D-016):
tokemetry's rate cards remain authoritative and the observed value is stored for
drift reporting, never replacing computed cost. It is copied onto the event's
computed-cost record at pricing time and surfaced by
`GET /api/v2/costs/reconciliation`, which reports observed-versus-computed
`drift_usd` and `drift_pct` per provider (`group_by=provider`, the default) or
per provider and UTC day (`group_by=day`). Only events carrying an
`observed_cost` participate; the percentage is null when computed cost is zero.

## Privacy (FR-TOK-029)

Payloads are **content-free**. Never send prompt or completion text, tool-call
arguments, file paths, code content, or reasoning text. Only counts, ids,
timestamps, model names, and enumerated states. The server rejects (or strips,
per its `privacy_mode`) events that carry disallowed content; a rejected event
is isolated as a poison event rather than failing the batch.

## Version negotiation

Every event and envelope declares `schema_version: 2`. The server serves the
current schema at `GET /api/v2/schemas/usage-event`; regenerate the client and
this mapping from it when the version increments. Unknown fields the server does
not recognize are rejected under `extra="forbid"`, so keep the client in sync
with the published schema.

## Operator runbook

Steps to provision, configure, and monitor a proxy exporter against a tokemetry
server. Commands use the bootstrap admin token only to *mint* the scoped ingest
token; the exporter itself never holds admin scopes.

### 1. Provision an ingest-only token

Mint a token scoped to ingest, not query or admin. `ingest:events` is required;
add `ingest:limits` if the proxy also reports provider rate-limit snapshots.
Bind it to the proxy's source identity with `source_allowlist` so a leaked token
cannot impersonate a different source (FR-AUTH). Ingest is denied (`403`) for any
event whose `source.name` is outside the allowlist.

```sh
curl -sS -X POST "$TOKEMETRY_URL/api/v1/tokens" \
  -H "Authorization: Bearer $BOOTSTRAP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label":"relayplane-proxy","scopes":["ingest:events","ingest:limits"],
       "source_allowlist":["aiProviderProxy"]}'
# -> {"token":"tkm_...","label":"relayplane-proxy", ...}
```

Store the returned `token` in the proxy's secret store; the token is shown once.

### 2. Configure the exporter

Point the exporter at the server and reference the token **by secret handle**,
never inline in committed config:

- `baseUrl`: `$TOKEMETRY_URL` (the exporter appends `/api/v2/ingest/events`).
- `token`: a reference to the secret (env var / secret manager key), not the
  literal value.
- `queuePath`: a durable on-disk queue directory so events survive restarts and
  are retried with backoff (the client pauses on `401`, retries `429`/`5xx`, and
  isolates poison events on `400`/`422`).
- `source`: `{ type: "gateway", name: "aiProviderProxy", version: "<build>" }`,
  matching the `source_allowlist` above.

### 3. Verify connectivity

Dry-run a sample event against the validate endpoint before enabling live
export. It runs the schema and privacy checks and **never persists**, returning
`{ "valid": true, "errors": [] }` on success:

```sh
curl -sS -X POST "$TOKEMETRY_URL/api/v2/ingest/validate" \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"schema_version":2,"events":[ ...one UsageEventV2... ]}'
```

`GET /api/v2/ready` (unauthenticated) confirms the server and its migrations are
healthy before you start.

### 4. Monitor exporter health

`GET /api/v2/sources` lists every source with a `health` block:
`last_successful_ingest`, `recent_error_count`, `reported_schema_version`,
`clock_skew_seconds`, and a `stale` flag against `staleness_threshold_seconds`.
A gateway source is **stale** once no error-free ingest has landed within its
threshold (10 minutes by default; configurable via
`TOKEMETRY_SOURCE_STALE_GATEWAY_SECONDS`) -- so a silently stopped exporter
becomes visible. Filter with `?type=gateway` and `?stale=true` to alert on
silent proxies:

```sh
curl -sS "$TOKEMETRY_URL/api/v2/sources?type=gateway&stale=true" \
  -H "Authorization: Bearer $QUERY_TOKEN"
```

Watch `clock_skew_seconds` (a large value means the proxy's clock disagrees with
the server) and `reported_schema_version` (a drift from the current version
means the client needs regenerating). Track cost trust with
`GET /api/v2/costs/reconciliation` (see [Cost reconciliation](#cost-reconciliation)).
