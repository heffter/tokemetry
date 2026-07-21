# Collector setup: Linux

Run one collector per Linux machine that uses Claude Code. It tails the local
transcripts under `~/.claude`, polls the subscription limit windows, and
uploads to the server over WireGuard. It runs as a **systemd user service** so
it starts at login and survives crashes.

## Quick install (one command)

From a clone of the repository, one command installs uv and the collector,
writes the config, runs a dry-run check, and registers the systemd user
service:

```bash
deploy/collector/install.sh \
  --server-url http://10.10.0.1:8787 \
  --token tkm_your_token \
  --machine-name my-laptop
```

- Omit `--token` to install and scaffold a placeholder config without starting
  the service; edit `~/.config/tokemetry/collector.toml`, then re-run to finish.
  Re-running never overwrites an edited config, and it upgrades an existing
  install.
- Add `--no-service` to install and configure only (no systemd unit).
- `install.sh --help` shows all options.

The installer also runs `loginctl enable-linger` so the service keeps running
when you are logged out (it prints a `sudo` hint if that needs privileges). The
rest of this page is the manual, step-by-step path.

## 1. Install uv and the collector

```bash
# uv (Python toolchain); skip if already installed.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the collector (Python 3.12+). From a clone:
uv tool install ./apps/collector
# or straight from git:
uv tool install "tokemetry-collector @ git+https://github.com/heffter/tokemetry.git#subdirectory=apps/collector"
```

This puts `tokemetry-collector` at `~/.local/bin/tokemetry-collector`. Confirm
it is on your `PATH`:

```bash
which tokemetry-collector
```

## 2. Write the config

```bash
mkdir -p ~/.config/tokemetry
cp deploy/collector.example.toml ~/.config/tokemetry/collector.toml
chmod 600 ~/.config/tokemetry/collector.toml
```

Edit `~/.config/tokemetry/collector.toml`:

- `server_url` — the server's WireGuard address, e.g. `http://10.10.0.1:8787`.
- `api_token` — a token minted in the dashboard (Settings), or the bootstrap
  token for the first run.
- `machine_name` — a stable name for this machine in the dashboard.

The file holds a bearer token; keep it `chmod 600` and never commit it.

## 3. Verify before running as a service

```bash
# Parse and report what would upload, changing nothing:
tokemetry-collector --config ~/.config/tokemetry/collector.toml --dry-run

# One-time historical import, then a single real cycle:
tokemetry-collector --config ~/.config/tokemetry/collector.toml --bootstrap --once
```

Open the dashboard; this machine should now appear with usage.

## 4. Register the systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp deploy/collector/systemd/tokemetry-collector.service ~/.config/systemd/user/
# Edit ExecStart if your install path differs from ~/.local/bin.
systemctl --user daemon-reload
systemctl --user enable --now tokemetry-collector

# Keep the service running when you are logged out:
loginctl enable-linger "$USER"
```

## 5. Logs and troubleshooting

```bash
# Live logs:
journalctl --user -u tokemetry-collector -f

# Status:
systemctl --user status tokemetry-collector

# Restart after a config change:
systemctl --user restart tokemetry-collector
```

- **No data in the dashboard.** Confirm `server_url` is reachable over
  WireGuard (`curl $server_url/api/v1/summary/overview -H "Authorization:
  Bearer <token>"`) and that `api_token` is valid.
- **Service not running after logout.** Ensure `loginctl enable-linger` was
  run for your user.
- **Permission errors reading transcripts.** The service runs as *your* user;
  confirm you are the account that runs Claude Code.

The collector is offline- and crash-safe: usage is queued locally in SQLite and
uploaded when the server is reachable. The server deduplicates, so re-runs
never double-count. The Anthropic OAuth token never leaves the machine; only
utilization percentages are uploaded.
