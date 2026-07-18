# Ingest API

Collectors push data to three idempotent, authenticated endpoints. The
OpenAPI schema is served at `/docs` (Swagger UI) and `/openapi.json`.

## Authentication

Every endpoint (except `GET /api/v1/health`) requires a bearer token:

```
Authorization: Bearer <token>
```

A token is valid if it matches the configured bootstrap token
(`TOKEMETRY_API_BOOTSTRAP_TOKEN`, for first-run setup) or a non-revoked row
in `api_tokens` (looked up by SHA-256 hash). Invalid or missing tokens get
`401`.

## Endpoints

### `POST /api/v1/ingest/events`

Batch of usage events. Idempotent: the server deduplicates on
`(provider, event_id)` keeping the row with the most output tokens, both
within the batch and against already-stored rows. Re-sending a streaming
snapshot with fewer output tokens never lowers a stored value.

```json
{
  "machine": {"name": "box-1", "platform": "windows", "collector_version": "0.1.0"},
  "events": [
    {
      "event_id": "req_1", "provider": "anthropic",
      "native_model": "claude-fable-5", "ts": "2026-07-09T09:41:14+00:00",
      "input_tokens": 10, "output_tokens": 365,
      "cache_read_tokens": 25502, "cache_write_long_tokens": 8583
    }
  ]
}
```

Response: `{"accepted": 1, "duplicates_merged": 0}`.

### `POST /api/v1/ingest/limits`

Batch of limit-window snapshots (append-only). Each carries
`provider`, `window_kind`, `utilization_pct`, and optional `resets_at`.

### `POST /api/v1/ingest/bootstrap`

Batch of historical daily aggregates imported once from a provider's local
stats cache. Upserted into `daily_rollups` on the grain
`(day, provider, machine, model, project='')` with `provenance='stats_cache'`;
re-importing the same cache is idempotent (replace, not accumulate).

## v2 ingest (provider-neutral)

The v2 endpoints accept the provider-neutral usage event (see
[usage-event-v2.md](../architecture/usage-event-v2.md)). They share the bearer
auth above (an `ingest:events` scope is added in Task 63) and are rate limited
in a class separate from query traffic (FR-INGEST-015). Request bodies may be
gzip-compressed (`Content-Encoding: gzip`, FR-INGEST-010). Every response
carries an `X-Request-ID` header (FR-INGEST-016).

### `POST /api/v2/ingest/events`

Batch envelope: `{ "schema_version": 2, "events": [ ...UsageEventV2... ],
"return_ids": false, "correction": false }`. Each event carries its own
`source`. The batch is validated (schema then privacy) and, if clean, persisted
in one transaction through the revision engine. The response reports the
server-generated `batch_id`, the `request_id`, and the five outcome counts
(`accepted`, `updated`, `duplicate`, `rejected`, `corrected`); with
`return_ids` it also echoes the accepted/updated ids, capped with an
`ids_truncated` flag (FR-INGEST-009). `correction: true` authorizes a
final-over-final correction (needs the `admin:corrections` scope once Task 63
lands). Limits: `ingest_max_events` (default 1000) and `ingest_max_bytes`
(default 5 MiB), both settings-driven (FR-INGEST-005).

### `POST /api/v2/ingest/validate`

Runs the same schema and privacy checks and returns `{ "valid": bool,
"errors": [...], "request_id": ... }` **without persisting anything**
(FR-INGEST-007), so an exporter can pre-flight a batch.

### `GET /api/v2/ready`

Unauthenticated readiness probe reporting `{ "status", "database", "migration" }`
without secrets (FR-INGEST-018/019); `503` when the database is unreachable.

## Errors

- `422` -- malformed payload (schema violation, empty batch, unknown field).
  The whole batch is rejected; ingest is all-or-nothing. v2 `/events` returns a
  structured `detail` of `{ "errors": [ {index, field_path, code, message} ],
  "request_id" }` (FR-INGEST-006).
- `413` -- v2 batch over the event-count or byte-size limit.
- `429` -- v2 ingest rate limit exceeded.
- `400` -- sanity-check failure (token count above the sane maximum,
  timestamp too far in the future) or a malformed/invalid-gzip v2 body.
- `401` -- missing or invalid bearer token.

## Notes

Costs are not computed at this stage: events are stored with `cost_usd`
null and priced by the cost engine (a later task). Conversation content is
never sent or stored; only usage metadata and counters.
