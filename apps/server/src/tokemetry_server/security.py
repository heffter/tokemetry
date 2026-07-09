"""Bearer-token hashing and generation.

API tokens are stored only as SHA-256 hashes; the plaintext is shown once
at creation and never persisted. SHA-256 (not a slow KDF) is appropriate
here because tokens are high-entropy random strings, not user passwords, so
brute-force is infeasible without the slow-hash overhead.
"""

from __future__ import annotations

import hashlib
import secrets

#: Plaintext token prefix, making leaked tokens easy to recognize and scan.
TOKEN_PREFIX = "tkm_"


def generate_token() -> str:
    """Return a new random bearer token with the tokemetry prefix."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the hex SHA-256 hash of ``token`` for storage/lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
