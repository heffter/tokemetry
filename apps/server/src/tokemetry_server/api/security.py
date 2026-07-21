"""HTTP transport-hardening helpers (Task 70.5).

Pure helpers for the security middleware in :mod:`tokemetry_server.app`: the
static secure-response headers, ingest-vs-query path classification (so the two
rate-limit classes stay separate and ingest bursts never starve query reads),
and the per-credential rate-limit key. Kept here so the classification and
keying are unit-testable without standing up the app.
"""

from __future__ import annotations

from starlette.requests import Request

#: Secure headers sent on every response (NFR-SEC-006). HSTS is added
#: separately, only when TLS is terminated in front of the app.
SECURE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}

#: HSTS value used when ``enable_hsts`` is set (two years, include subdomains).
HSTS_VALUE = "max-age=63072000; includeSubDomains"


def is_api_path(path: str) -> bool:
    """Whether a path is a rate-limited/guarded API route."""
    return path.startswith("/api/")


def is_ingest_path(path: str) -> bool:
    """Whether a path is ingest traffic (already limited by the ingest bucket)."""
    return "/ingest" in path


def is_health_path(path: str) -> bool:
    """The unauthenticated liveness probe, never rate-limited."""
    return path == "/api/v1/health"


def rate_key(request: Request) -> str:
    """A stable per-credential key: the bearer token, else the client host."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return "token:" + auth[7:].strip()
    client = request.client
    return "host:" + (client.host if client is not None else "unknown")
