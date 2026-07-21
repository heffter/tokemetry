# Collector

The collector is a small, synchronous daemon that runs on every machine. It
tails usage sources, polls limit sources, and uploads everything to the
server through a durable local queue.

## Design guarantees

- **Crash-safe.** Parse progress (byte offsets per file) and the upload
  queue live in a single SQLite database. Offsets advance only after a
  successful parse; batches leave the queue only after the server confirms
  receipt. A crash at any point loses nothing.
- **Offline-safe.** When the server is unreachable, batches stay queued and
  upload on a later cycle. The daemon never crashes on network errors.
- **Exactly-once (effectively).** The server deduplicates by
  `(provider, event_id)` keeping the max-output row, so re-uploading a batch
  after an ambiguous failure is harmless.
- **Provider-agnostic.** The runner drives the `UsageSource` / `LimitsSource`
  interfaces; sources are built from config by name (`sources.py`). Adding a
  provider needs no runner change.

## Cycle

Each cycle (`collect_once`):

1. **Tail** every usage source: `discover()` files, compare each file's size
   to the stored offset (reset to 0 if the file shrank -- rotation), `parse`
   from the offset, enqueue events in `upload_batch_size` chunks, persist the
   new offset.
2. **Poll** limit sources (on a slower cadence in daemon mode); unavailable
   sources are skipped, not fatal.
3. **Drain** the queue: upload oldest-first until one fails (server down) or
   the queue empties.

## Modes (CLI)

```
tokemetry-collector --config collector.toml            # daemon loop
tokemetry-collector --config collector.toml --once     # one cycle, exit
tokemetry-collector --config collector.toml --dry-run  # parse + report only
tokemetry-collector --config collector.toml --bootstrap  # one-time history import
```

`--dry-run` parses and reports counts without changing state or contacting
the server. `--bootstrap` runs the one-time historical import (guarded by a
state flag so it never re-imports).

## Configuration

TOML file; see `deploy/collector.example.toml`. Key fields: `server_url`,
`api_token`, `machine_name`, `poll_interval_seconds`,
`limits_poll_interval_seconds`, `state_db_path`, and `[sources.*]` /
`[limits.*]` tables with `enabled` flags. Secrets (the API token) live only
in this file on the machine; never commit it.

## Built-in sources

| Config key | Kind | Implementation |
|---|---|---|
| `[sources.claude_code]` | usage | `ClaudeCodeJsonlSource` -- tails `~/.claude` transcripts (optional `claude_home` override). |
| `[limits.anthropic_oauth]` | limits | `AnthropicOAuthLimitsSource` -- polls the OAuth usage endpoint. |
| `[limits.openai_codex]` | limits | `OpenAICodexLimitsSource` -- polls the Codex usage endpoint (off by default). |
| `[limits.zai_coding_plan]` | limits | `ZaiCodingLimitsSource` -- polls the Z.ai coding-plan quota endpoint (off by default). |

### Anthropic OAuth limits

`AnthropicOAuthLimitsSource` reads the OAuth access token from
`~/.claude/.credentials.json` and calls the undocumented
`GET https://api.anthropic.com/api/oauth/usage` endpoint (headers:
`Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, a
Claude-Code-like `User-Agent`). It maps the `five_hour`, `seven_day`,
`seven_day_opus`, and `seven_day_sonnet` windows into normalized
`LimitSnapshot`s with `provenance='official'`.

The token never leaves the machine -- only utilization percentages and reset
times are uploaded. The endpoint is unofficial and may change; every failure
(missing token, network error, non-2xx, unparseable body) raises
`LimitsUnavailableError`, and the collector degrades to local estimates
rather than crashing.

### OpenAI/Codex limits (`[limits.openai_codex]`, off by default)

`OpenAICodexLimitsSource` reads the Codex access token and account id from
`auth.json` under the Codex home (`$CODEX_HOME` or `~/.codex`; override with
`codex_home`) and polls the undocumented Codex usage endpoint. It maps the
`primary` and `secondary` subscription windows to `LimitSnapshot`s with
`provider='openai'`, `provenance='official'`, and the account label. Poll
cadence follows `limits_poll_interval_seconds`. The token never leaves the
machine; every failure (missing credentials, expired auth, endpoint change,
network, malformed body) raises `LimitsUnavailableError`, so a broken source is
skipped and the others keep uploading.

Limit snapshots are uploaded to `POST /api/v2/ingest/limits` (Task 76), which
carries the `account`, `organization`, `source`, `limit_amount`, `remaining`,
and `unit` dimensions so they land in their dedicated `limit_snapshots` columns
rather than in `raw`. The server keeps the v1 limits endpoint for older
collectors.

### Z.ai coding-plan limits (`[limits.zai_coding_plan]`, off by default)

`ZaiCodingLimitsSource` reads the Z.ai `api_key` and account from `config.json`
under the Z.ai home (`$ZAI_HOME` or `~/.zai`; override with `zai_home`) and polls
the undocumented coding-plan quota endpoint. It maps the `prompt_5h` quota
window to a `LimitSnapshot` with `provider='zai'`, `provenance='official'`. Same
failure behavior: the key never leaves the machine and any error degrades
gracefully to `LimitsUnavailableError`.

Both sources' window kinds carry registry labels
(see [architecture/limits-v2.md](../architecture/limits-v2.md)), so their
windows appear on the dashboard without any dashboard code change.
