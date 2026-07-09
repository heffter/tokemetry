"""tokemetry collector: per-machine usage collection daemon.

Runs on every machine, discovers enabled usage sources through the provider
registry (Claude Code JSONL transcripts first), tails them incrementally,
polls provider limit endpoints, buffers everything in a local SQLite queue,
and uploads batches to the tokemetry server. Designed to run as a Windows
Scheduled Task, systemd unit, or launchd agent.
"""

__version__ = "0.1.0"
