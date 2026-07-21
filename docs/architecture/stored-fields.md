# Stored fields (privacy review)

A catalogue of what tokemetry persists, for privacy review (FR-PRIV-005). The
governing rule: **only metadata, counts, and catalogue identifiers are
stored -- never prompt/response content.** Content-bearing keys are rejected at
ingest ([privacy validator](../../apps/server/src/tokemetry_server/services/privacy.py),
FR-PRIV-012), and free-form maps (`extra`, `dimensions`) are size- and
depth-bounded and content-key-scanned.

## Usage events (`usage_events_v2`)

Per attempt. No content.

- **Identity/grain**: `provider`, `event_id`, `schema_version`, `event_kind`,
  `finality`, `sequence`, `logical_request_id`, `attempt_id`,
  `provider_request_id`, `provider_response_id`.
- **Model**: `requested_model`, `routed_model`, `native_model` (catalogue
  identifiers, not content).
- **Attribution**: `machine`, `project`, `session_id`, `agent_id`,
  `environment`. These are operator- or exporter-supplied labels and **may be
  pseudonymized** (hashed) by the collector/proxy before they are sent; the
  server treats them as opaque strings (FR-PRIV-004).
- **Counts/measures**: the six token tiers, `success`, `outcome`, `http_status`,
  `stop_reason`, `service_tier`, `streaming`, latency and timing, tool-call
  count, cost columns.
- **Free-form**: `routing`, `dimensions`, `extra` (allow-listed dimension keys;
  content keys rejected), `tool_histogram` (tool *names* only, off by default).
  `trace_id`/`span_id`/`parent_span_id` for correlation.

## Derived and operational

- `computed_costs`, `billable_units`, `daily_rollups`, `logical_requests`,
  `usage_event_revisions` -- counts and amounts derived from events; no content.
- `limit_snapshots` -- provider quota utilization (numbers only).
- `ingest_batches` -- per-batch counts and the response `request_id`.
- `data_quality_events` -- anomaly `kind`/`subject`/`detail` (metadata only).
- `sources`, `providers`, `models`, `model_aliases`, `rate_cards`, `pricing`,
  `billable_units` -- catalogue/registry rows.

## Security and settings

- `api_tokens` -- **hashed** token (`token_hash`), never the plaintext; `label`,
  `scopes`, `source_allowlist`, timestamps, `revoked`. The plaintext is returned
  once at creation and never stored or echoed (FR-PRIV-011).
- `audit_log` -- who did what, with a content-free `detail`; secrets never
  appear (NFR-SEC-005).
- `app_settings` -- runtime settings including channel secrets; secret fields
  are masked in API responses.
- `retention_status` -- per-category retention worker counters.

## Not stored

Prompt or response text, tool arguments, file paths/contents, code, or any
message body. Connection secrets (provider API keys, OAuth tokens) never leave
the collector/proxy and are never sent to the server.
