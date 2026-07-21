# Collector setup: macOS

Run one collector per Mac that uses Claude Code. It tails the local transcripts
under `~/.claude`, polls the subscription limit windows, and uploads to the
server over WireGuard. It runs as a **launchd agent** so it starts at login and
restarts on crash.

## Quick install (one command)

From a clone of the repository, one command installs uv and the collector,
writes the config, runs a dry-run check, and loads the launchd agent:

```bash
deploy/collector/install.sh \
  --server-url http://10.10.0.1:8787 \
  --token tkm_your_token \
  --machine-name my-mac
```

- Omit `--token` to install and scaffold a placeholder config without starting
  the agent; edit `~/.config/tokemetry/collector.toml`, then re-run to finish.
  Re-running never overwrites an edited config, and it upgrades an existing
  install.
- Add `--no-service` to install and configure only (no launchd agent).
- `install.sh --help` shows all options.

If your transcripts live under a protected location, you may still need to grant
Full Disk Access (see step 5). The rest of this page is the manual,
step-by-step path.

## 1. Install uv and the collector

```bash
# uv (Python toolchain); skip if already installed.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the collector (Python 3.12+). From a clone:
uv tool install ./apps/collector
# or straight from git:
uv tool install "tokemetry-collector @ git+https://github.com/heffter/tokemetry.git#subdirectory=apps/collector"
```

This puts `tokemetry-collector` at `~/.local/bin/tokemetry-collector`. Confirm:

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

## 3. Verify before running as a service

```bash
tokemetry-collector --config ~/.config/tokemetry/collector.toml --dry-run
tokemetry-collector --config ~/.config/tokemetry/collector.toml --bootstrap --once
```

The machine should appear in the dashboard with usage.

## 4. Register the launchd agent

```bash
cp deploy/collector/launchd/com.tokemetry.collector.plist ~/Library/LaunchAgents/
```

Edit `~/Library/LaunchAgents/com.tokemetry.collector.plist` and replace every
`CHANGE_ME` with your macOS username (three paths: the executable, the config,
and the log file). Then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.tokemetry.collector.plist
```

To apply a later change to the plist, unload then load again:

```bash
launchctl unload ~/Library/LaunchAgents/com.tokemetry.collector.plist
launchctl load   ~/Library/LaunchAgents/com.tokemetry.collector.plist
```

## 5. Logs and troubleshooting

```bash
# The plist writes stdout/stderr here:
tail -f ~/Library/Logs/tokemetry-collector.log

# Confirm the agent is loaded:
launchctl list | grep tokemetry
```

- **`Operation not permitted` reading `~/.claude`.** Grant the terminal (or the
  collector binary) Full Disk Access under System Settings > Privacy &
  Security if your transcripts live under a protected location.
- **No data in the dashboard.** Confirm `server_url` is reachable over
  WireGuard and `api_token` is valid.
- **Agent not starting at login.** Re-check the `ProgramArguments` paths in the
  plist match the actual install location from step 1.

The collector is offline- and crash-safe: usage is queued locally in SQLite and
uploaded when the server is reachable. The server deduplicates, so re-runs
never double-count. The Anthropic OAuth token never leaves the machine; only
utilization percentages are uploaded.
