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

## Errors

- `422` -- malformed payload (schema violation, empty batch, unknown field).
  The whole batch is rejected; ingest is all-or-nothing.
- `400` -- sanity-check failure (token count above the sane maximum,
  timestamp too far in the future).
- `401` -- missing or invalid bearer token.

## Notes

Costs are not computed at this stage: events are stored with `cost_usd`
null and priced by the cost engine (a later task). Conversation content is
never sent or stored; only usage metadata and counters.
