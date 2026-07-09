"""tokemetry server: central ingest and query service.

FastAPI application that receives usage events and limit snapshots from
collectors, stores them in Postgres (SQLite for development), computes
cost/block/burn-rate analytics, evaluates alert rules, and exposes the full
REST + WebSocket API consumed by the dashboard and third-party clients.
"""

__version__ = "0.1.0"
