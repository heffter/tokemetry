# Sources and token administration API

The v2 sources API exposes the reporting-source registry with health, and the
token API (v1) mints least-privilege scoped tokens. Both are part of Epic TOK-4.
See [source-health.md](../architecture/source-health.md) for the health model.

## Sources

Sources are auto-registered from v2 ingest payloads (a gateway, collector, SDK,
importer, or manual actor); identity is `(type, name, instance_id)` and is never
conflated with a machine. Listing needs `query:read`; mutation and revocation
need `admin:tokens`. No token hashes or secrets are ever returned.

### `GET /api/v2/sources`

Returns every source joined with its query-time health:

```json
{
  "id": 7, "type": "gateway", "name": "aiProviderProxy", "version": "1.2.3",
  "instance_id": "proxy-01", "machine": "devbox-01", "token_label": "proxy-token",
  "billing_mode": "api_billed", "first_seen": "...", "last_seen": "...",
  "revoked": false,
  "health": {
    "stale": false, "last_successful_ingest": "...", "recent_error_count": 0,
    "reported_schema_version": 2, "clock_skew_seconds": 0.0,
    "staleness_threshold_seconds": 600.0
  }
}
```

Filter with `?type=<collector|gateway|sdk|importer|manual>` and `?stale=true|false`
(FR-SOURCE-006).

### `PATCH /api/v2/sources/{id}`

Body `{ "token_label"?, "billing_mode"? }`. Mutates the label and billing mode
(`api_billed`|`subscription`) **without changing event identity** (FR-SOURCE-010);
existing events keep their `source_id`. An unknown billing mode returns `400`.

### `POST /api/v2/sources/{id}/revoke`

Marks the source revoked so its future events are refused (when a token
allowlist enforces it), while **all historical events are retained**
(FR-SOURCE-012).

## Tokens

`POST /api/v1/tokens` mints a token, returning the plaintext **once**. The body
accepts `scopes` (defaulting to the full set for compatibility) and an optional
`source_allowlist`. Provisioning an ingest-only token for the proxy is a single
call:

```json
{ "label": "proxy-ingest", "scopes": ["ingest:events"], "source_allowlist": ["aiProviderProxy"] }
```

`GET /api/v1/tokens` lists metadata (label, scopes, `last_used`, `revoked`,
allowlist) but never the hash or secret (FR-PRIV-011). `DELETE
/api/v1/tokens/{label}` revokes.

### Rotation (FR-SEC-009)

There is no in-place secret rotation; rotate by minting a replacement and
revoking the old token:

1. `POST /api/v1/tokens` with a new label and the same scopes/allowlist -- record
   the new plaintext.
2. Deploy the new token to the client.
3. `DELETE /api/v1/tokens/{old-label}` once the client is confirmed switched.

Because tokens are attributable to a source or source group, the source's
history is unaffected by rotation.
