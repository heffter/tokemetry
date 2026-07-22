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
| `backup` | nightly `pg_dump` to the `backups` volume, 14-day file retention (`deploy/backup.sh`) |

By default the background retention worker is **disabled**
(`TOKEMETRY_RETENTION_WORKER_ENABLED=false`), so usage rows are never pruned and
only backup files age out. To enforce policy-based row retention (the PRD
defaults are 180-day raw events, etc.), set
`TOKEMETRY_RETENTION_WORKER_ENABLED=true` — the Compose file passes it through,
so add it to `.env`. See [data retention](../architecture/retention.md) for the
policy and per-category defaults.

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

## Transport hardening

The API is designed to run behind WireGuard (bound to the WireGuard address,
never the public interface). The app also enforces, in-process (Task 70.5):

- **Rate limiting** — separate token buckets for ingest and query traffic so an
  ingest burst never starves query reads. A limited request gets `429` with a
  `Retry-After` header. Tune with `TOKEMETRY_INGEST_RATE_CAPACITY` /
  `_INGEST_RATE_PER_SECOND` and `TOKEMETRY_QUERY_RATE_CAPACITY` /
  `_QUERY_RATE_PER_SECOND`.
- **Request-size cap** — bodies over `TOKEMETRY_MAX_REQUEST_BYTES` (default
  4 MiB) are refused with `413`. JSON nesting depth is bounded on v2 events
  (`TOKEMETRY_PRIVACY_MAX_JSON_DEPTH`); every other endpoint uses strict
  fixed-shape request schemas, so arbitrarily nested payloads are rejected by
  validation.
- **CORS** — no cross-origin browser access by default (the dashboard is served
  same-origin). Grant specific origins with `TOKEMETRY_CORS_ALLOW_ORIGINS` (a
  comma-separated allowlist).
- **Secure headers** — every response carries `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, and `Cross-Origin-Opener-Policy`.
- **WebSocket cap** — at most `TOKEMETRY_WS_MAX_CONNECTIONS_PER_TOKEN` (default
  8) concurrent stream connections per token.

**TLS**: terminate TLS in front of the app for any deployment reachable beyond
WireGuard (a reverse proxy such as Caddy or nginx). When TLS is in place, set
`TOKEMETRY_ENABLE_HSTS=true` so responses advertise `Strict-Transport-Security`.
Never expose the app over plaintext on a public interface; bearer tokens must
only travel over an encrypted channel.

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

## Native systemd (without Docker)

Docker Compose above is the primary path. If you would rather run directly on
the host (for example on a small VPS without a container runtime), use the
native systemd unit. This runs the app under uvicorn from a virtualenv and,
by default, stores data in SQLite.

```bash
# 1. Install as a dedicated user under /opt/tokemetry.
sudo useradd --system --home /opt/tokemetry --shell /usr/sbin/nologin tokemetry
sudo git clone https://github.com/heffter/tokemetry.git /opt/tokemetry
sudo chown -R tokemetry:tokemetry /opt/tokemetry

# 2. Create the virtualenv and install the server + build the dashboard.
cd /opt/tokemetry
sudo -u tokemetry uv venv apps/server/.venv
sudo -u tokemetry uv pip install --python apps/server/.venv ./apps/server
sudo -u tokemetry npm --prefix apps/dashboard ci
sudo -u tokemetry npm --prefix apps/dashboard run build

# 3. Data directory (for the SQLite database).
sudo install -d -o tokemetry -g tokemetry /var/lib/tokemetry

# 4. Environment file with the secrets (mode 0600).
sudo install -d /etc/tokemetry
sudo cp deploy/server/tokemetry-server.env.example /etc/tokemetry/server.env
sudo chmod 600 /etc/tokemetry/server.env
# Edit /etc/tokemetry/server.env: set TOKEMETRY_BIND_HOST to the WireGuard
# address, a long random TOKEMETRY_API_BOOTSTRAP_TOKEN, and adjust paths.

# 5. Install and start the unit.
sudo cp deploy/server/systemd/tokemetry-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tokemetry-server
```

Logs: `journalctl -u tokemetry-server -f`. Upgrade with
`git pull`, re-install/re-build (steps 2), then
`sudo systemctl restart tokemetry-server`. Migrations still run on startup.
