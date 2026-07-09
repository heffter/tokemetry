# Deploying a collector

Run one collector per machine that uses Claude Code. It tails the local
transcripts and polls the limit endpoint, uploading to the server over
WireGuard.

## Install

The collector is a Python package. Install it with pipx or uv (Python 3.12+):

```bash
uv tool install "tokemetry-collector @ git+https://github.com/heffter/tokemetry.git#subdirectory=apps/collector"
# or, from a clone:
uv tool install ./apps/collector
```

This puts `tokemetry-collector` on your PATH.

## Configure

```bash
mkdir -p ~/.config/tokemetry
cp deploy/collector.example.toml ~/.config/tokemetry/collector.toml
# Edit: server_url (the WireGuard address), api_token (mint one in the
# dashboard, or use the bootstrap token to start), and machine_name.
```

Verify it can see your usage without uploading anything:

```bash
tokemetry-collector --config ~/.config/tokemetry/collector.toml --dry-run
```

Import history once (from Claude Code's stats cache), then run normally:

```bash
tokemetry-collector --config ~/.config/tokemetry/collector.toml --bootstrap --once
```

## Run as a service

### Linux (systemd user service)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/collector/systemd/tokemetry-collector.service ~/.config/systemd/user/
# Edit the ExecStart path if your install location differs.
systemctl --user daemon-reload
systemctl --user enable --now tokemetry-collector
loginctl enable-linger "$USER"   # keep it running when logged out
```

### macOS (launchd agent)

```bash
cp deploy/collector/launchd/com.tokemetry.collector.plist ~/Library/LaunchAgents/
# Edit the CHANGE_ME paths for your user.
launchctl load ~/Library/LaunchAgents/com.tokemetry.collector.plist
```

### Windows (Scheduled Task)

Run in PowerShell as the user who runs Claude Code:

```powershell
deploy\collector\windows\Register-Collector.ps1 `
  -CollectorPath "$env:USERPROFILE\.local\bin\tokemetry-collector.exe" `
  -ConfigPath   "$env:USERPROFILE\.config\tokemetry\collector.toml"
Start-ScheduledTask -TaskName tokemetry-collector
```

## Notes

- The collector is offline- and crash-safe: usage is queued locally in SQLite
  and uploaded when the server is reachable; the server deduplicates, so
  re-runs never double-count.
- The Anthropic OAuth token never leaves the machine; only utilization
  percentages are uploaded (see [collector overview](../collector/overview.md)).
