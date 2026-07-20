# Alerting

The server evaluates alert rules on a timer (`TOKEMETRY_ALERTS_INTERVAL_SECONDS`,
default 60) and on demand via the API. Each firing is recorded in
`alert_events` and dispatched to the rule's channels.

## Rule kinds

| Kind | Fires when | Threshold |
|---|---|---|
| `limit_pct` | a limit window's utilization reaches the threshold | percent (default 80); `window_kind` selects the window |
| `predicted_exhaustion` | the 5-hour block is predicted to hit 100% before it resets | — |
| `burn_rate` | token burn rate exceeds the threshold | tokens/min (default 5000) |
| `collector_stale` | a machine has not reported within the threshold | minutes (default 30) |
| `stale_source` | a reporting source has not ingested within the threshold | minutes (default: the source type's staleness threshold, e.g. 30 for collectors, 10 for gateways; critical at 4x) |
| `unpriced_events` | active costs in the last day are unpriced or partially priced | event count (warn default 1, crit 100) |
| `unknown_model` | events in the last day used a model the registry does not know | event count (warn default 1, crit 25) |

Severity is derived (for example `limit_pct` is `warning`, or `critical` at
95%+). Rules are stored in `alert_rules`; their logic is selected by `kind`.

`unpriced_events` and `unknown_model` are the accounting-gap alerts and are
distinct: a **known** model that merely lacks a rate card is *unpriced* (its
computed cost is `unpriced`/`partial`), while a model the registry does not
recognize (lifecycle `unknown`, or never registered) is *unknown*. Both honor
dimension filters, name the top offending `(provider, model)` pairs in their
content-free context, and report the open data-quality record count for the gap
they summarize.

`stale_source` fires **one alert per source**, each tracked independently: a
source re-fires only after its own cooldown and sends its own recovery notice
when it ingests again, so several stale sources never suppress one another.
Revoked sources never fire. The rule's `source` dimension filter (a list of
source names under `config.filters.source`) scopes which sources it watches;
staleness measures the time since a source's last successful ingest (or its
first sighting, if it has never ingested successfully).

## Suppression

- **Cooldown** (`cooldown_seconds`): a rule will not fire again until the
  cooldown since its last event has elapsed.
- **Quiet hours** (`quiet_hours = {"start_hour": 22, "end_hour": 7}`, UTC):
  the rule is skipped while the current hour is inside the window (wrapping
  past midnight is handled).

## Channels

A rule targets channels by name (`["ntfy", "telegram", "smtp"]`). Connection
secrets live in server settings, never in the rule row:

- **ntfy** — `TOKEMETRY_NTFY_URL` (default `https://ntfy.sh`),
  `TOKEMETRY_NTFY_TOPIC`.
- **Telegram** — `TOKEMETRY_TELEGRAM_BOT_TOKEN`,
  `TOKEMETRY_TELEGRAM_CHAT_ID`.
- **SMTP** — `TOKEMETRY_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM`,
  `_TO`, `_USE_TLS`.

A channel only delivers when its required settings are present; a rule that
fires with no deliverable channel is still recorded (`delivered = false`).

## API

- `GET/POST /api/v1/alerts`, `PUT/DELETE /api/v1/alerts/{id}` — manage rules.
- `GET /api/v1/alerts/events` — recent history.
- `POST /api/v1/alerts/evaluate` — run the engine now and return what fired.

The dashboard's Alerts view wraps these: create common rules, delete, view
history, and trigger an evaluation.
