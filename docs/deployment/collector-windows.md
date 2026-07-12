# Collector setup: Windows

Run one collector per Windows machine that uses Claude Code. It tails the local
transcripts under `%USERPROFILE%\.claude`, polls the subscription limit
windows, and uploads to the server over WireGuard. It runs as a **Scheduled
Task** that starts at logon and restarts on crash.

Run every step in a PowerShell session as the user who runs Claude Code (the
collector reads that user's `%USERPROFILE%\.claude`).

## 1. Install uv and the collector

```powershell
# uv (Python toolchain); skip if already installed.
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Install the collector (Python 3.12+). From a clone:
uv tool install .\apps\collector
# or straight from git:
uv tool install "tokemetry-collector @ git+https://github.com/heffter/tokemetry.git#subdirectory=apps/collector"
```

This puts `tokemetry-collector.exe` under `%USERPROFILE%\.local\bin`. Confirm:

```powershell
Get-Command tokemetry-collector
```

## 2. Write the config

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\tokemetry" | Out-Null
Copy-Item deploy\collector.example.toml "$env:USERPROFILE\.config\tokemetry\collector.toml"
```

Edit `%USERPROFILE%\.config\tokemetry\collector.toml`:

- `server_url` — the server's WireGuard address, e.g. `http://10.10.0.1:8787`.
- `api_token` — a token minted in the dashboard (Settings), or the bootstrap
  token for the first run.
- `machine_name` — a stable name for this machine in the dashboard.

Use forward slashes or escaped backslashes in any Windows path inside the TOML
(for example `claude_home = "C:/Users/you/.claude"`).

## 3. Verify before running as a service

```powershell
tokemetry-collector --config "$env:USERPROFILE\.config\tokemetry\collector.toml" --dry-run
tokemetry-collector --config "$env:USERPROFILE\.config\tokemetry\collector.toml" --bootstrap --once
```

The machine should appear in the dashboard with usage.

## 4. Register the Scheduled Task

```powershell
deploy\collector\windows\Register-Collector.ps1 `
  -CollectorPath "$env:USERPROFILE\.local\bin\tokemetry-collector.exe" `
  -ConfigPath   "$env:USERPROFILE\.config\tokemetry\collector.toml"
Start-ScheduledTask -TaskName tokemetry-collector
```

The task is registered with an at-logon trigger and auto-restart, running as
the current interactive user.

### Alternative: run as a Windows Service with NSSM

A Scheduled Task runs only while the user is logged on. To run the collector as
a true background service (started at boot, no login required), use
[NSSM](https://nssm.cc/):

```powershell
nssm install tokemetry-collector `
  "$env:USERPROFILE\.local\bin\tokemetry-collector.exe" `
  "--config `"$env:USERPROFILE\.config\tokemetry\collector.toml`""
nssm set tokemetry-collector AppExit Default Restart
nssm start tokemetry-collector
```

Run the service under the account whose `.claude` transcripts you want to
collect (`nssm set tokemetry-collector ObjectName <user> <password>`), since the
collector reads that user's profile.

## 5. Logs and troubleshooting

```powershell
# Scheduled Task state and last run result:
Get-ScheduledTask -TaskName tokemetry-collector | Get-ScheduledTaskInfo

# Run one cycle in the foreground to see errors directly:
tokemetry-collector --config "$env:USERPROFILE\.config\tokemetry\collector.toml" --once
```

- **No data in the dashboard.** Confirm `server_url` is reachable over
  WireGuard and `api_token` is valid.
- **Task runs but nothing uploads.** Confirm the task's user matches the
  account that runs Claude Code; the collector reads that user's `.claude`.
- **Task does not start.** Check the last run result above; a `0x1` usually
  means a wrong `CollectorPath` or `ConfigPath`.

The collector is offline- and crash-safe: usage is queued locally in SQLite and
uploaded when the server is reachable. The server deduplicates, so re-runs
never double-count. The Anthropic OAuth token never leaves the machine; only
utilization percentages are uploaded.
