# Query API

All endpoints require a bearer token (see [ingest.md](ingest.md)). The full
OpenAPI schema is at `/docs` and `/openapi.json`.

## Summary and limits

- `GET /api/v1/summary/now` -- dashboard front page: current limit gauges,
  token burn rate (tokens/min, last 60 min), predicted exhaustion for the
  5-hour window, and today's totals by model.
- `GET /api/v1/limits/current` -- latest snapshot per limit window.
- `GET /api/v1/limits/history?window_kind=five_hour&hours=24` -- utilization
  time series for one window.
- `GET /api/v1/blocks?hours=120` -- reconstructed 5-hour blocks aligned to
  official reset times, each with token/cost totals, peak per-minute burn,
  and end utilization.

Predictions extrapolate the recent slope of official utilization rather than
guessing an absolute token budget, so they work for subscription plans whose
limits are not published.

## Usage aggregation

- `GET /api/v1/usage?group_by=<dim>&from=<date>&to=<date>` with optional
  `provider`, `machine`, `model`, `project` filters.
- `group_by`: `day`, `provider`, `model`, `machine`, `project` (served from
  `daily_rollups`), or `session` (served from `usage_events`).
- `GET /api/v1/sessions?limit=100` -- recent sessions, newest first.
- `GET /api/v1/machines` -- fleet view with per-machine totals and last-seen.
- `GET /api/v1/heatmap?from&to` -- daily calendar plus a weekday x hour punch
  card.
- `GET /api/v1/cost?from&to` -- total known cost and, when
  `TOKEMETRY_SUBSCRIPTION_MONTHLY_USD` is set, the value multiple vs the
  prorated subscription price.
- `GET /api/v1/pricing` -- the pricing table.

## API tokens

- `POST /api/v1/tokens` `{ "label": "openclaw" }` -> `201` with the plaintext
  token, shown only once.
- `GET /api/v1/tokens` -- token metadata (never the secrets).
- `DELETE /api/v1/tokens/{label}` -> `204`; revoked tokens are rejected.

Third-party apps (for example OpenClaw) use a minted token to read any query
endpoint -- the API exposes everything the dashboard shows.

## Live stream

- `WS /api/v1/stream?token=<token>` -- authenticated WebSocket; emits a JSON
  message per accepted ingest batch (`{"type": "events"|"limits", "machine",
  "accepted"}`). Best-effort live view; slow clients are dropped rather than
  blocking ingest.
