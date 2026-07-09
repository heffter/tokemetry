# Deploying the server

The server runs on your VPS as two containers (app + Postgres) behind
WireGuard. The app serves both the REST API and the built dashboard.

## Prerequisites

- Docker and the Compose plugin on the VPS.
- A running WireGuard interface; note its address (for example `10.10.0.1`).

## Steps

```bash
git clone https://github.com/heffter/tokemetry.git
cd tokemetry/deploy
cp .env.example .env
# Edit .env: set WG_ADDRESS to the WireGuard address, a strong
# POSTGRES_PASSWORD, and a long random TOKEMETRY_API_BOOTSTRAP_TOKEN.
docker compose up -d --build
```

The app is now reachable only on `http://<WG_ADDRESS>:8787` — the port is
published on the WireGuard address, never the public interface. Open that URL
from a machine on the VPN and enter the bootstrap token to connect.

## What runs

| Service | Role |
|---|---|
| `server` | FastAPI API + dashboard SPA (from `TOKEMETRY_STATIC_DIR=/app/static`), migrations on startup, alert loop |
| `db` | Postgres 16 with a persistent volume |
| `backup` | nightly `pg_dump` to the `backups` volume, 14-day retention (`deploy/backup.sh`) |

## Configuration

All settings are `TOKEMETRY_`-prefixed environment variables (see
`apps/server/src/tokemetry_server/config.py`). Common ones:

- `TOKEMETRY_DATABASE_URL` — set by compose to the Postgres async URL.
- `TOKEMETRY_API_BOOTSTRAP_TOKEN` — first-run/admin token; mint per-client
  tokens through the dashboard Settings view or `POST /api/v1/tokens`, then
  keep the bootstrap token for administration only.
- `TOKEMETRY_SUBSCRIPTION_MONTHLY_USD` — shows the value multiple.
- Alert channels — `TOKEMETRY_NTFY_TOPIC`, `TOKEMETRY_TELEGRAM_*`,
  `TOKEMETRY_SMTP_*` (see [alerting](../alerting.md)).

## Restore a backup

```bash
docker compose exec -T db psql -U tokemetry tokemetry \
  < <(gzip -dc /path/to/backups/tokemetry-<stamp>.sql.gz)
```

## Upgrades

```bash
git pull && docker compose up -d --build
```

Migrations run automatically on startup (`TOKEMETRY_AUTO_MIGRATE=true`).
