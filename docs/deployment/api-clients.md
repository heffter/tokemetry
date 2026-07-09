# API clients (OpenClaw and others)

Everything the dashboard shows is available through the REST API, so other
applications can consume the same data. This is how you provision access.

## Mint a token

Create a dedicated token per client so it can be revoked independently:

```bash
curl -s -X POST http://<WG_ADDRESS>:8787/api/v1/tokens \
  -H "Authorization: Bearer <bootstrap-or-admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"label": "openclaw"}'
# => {"label":"openclaw","token":"tkm_...","created_at":"..."}
```

The plaintext token is returned once. Store it in the client's config. You
can also mint and revoke tokens from the dashboard Settings view.

## Use it

Send the token as a bearer header on any query endpoint:

```bash
curl -s http://<WG_ADDRESS>:8787/api/v1/summary/now \
  -H "Authorization: Bearer tkm_..."
```

Useful read endpoints (full list at `/docs`):

- `GET /api/v1/summary/now` — gauges, burn rate, prediction, today.
- `GET /api/v1/usage?group_by=day|model|machine|project|session&from&to`
- `GET /api/v1/limits/current`, `GET /api/v1/blocks`
- `GET /api/v1/sessions`, `GET /api/v1/machines`, `GET /api/v1/cost`

## Revoke

```bash
curl -s -X DELETE http://<WG_ADDRESS>:8787/api/v1/tokens/openclaw \
  -H "Authorization: Bearer <admin-token>"
```

Revoked tokens are rejected immediately.

## Live stream

Clients that want push updates can open the WebSocket:

```
ws://<WG_ADDRESS>:8787/api/v1/stream?token=tkm_...
```

It emits one JSON message per accepted ingest batch
(`{"type": "events"|"limits", "machine", "accepted"}`).
