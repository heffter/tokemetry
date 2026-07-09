"""Unit tests for token generation and hashing."""

from tokemetry_server.security import TOKEN_PREFIX, generate_token, hash_token


def test_generated_token_has_prefix_and_entropy() -> None:
    token = generate_token()
    assert token.startswith(TOKEN_PREFIX)
    assert len(token) > len(TOKEN_PREFIX) + 30


def test_generated_tokens_are_unique() -> None:
    assert generate_token() != generate_token()


def test_hash_is_deterministic_and_hex() -> None:
    token = "tkm_example"
    digest = hash_token(token)
    assert digest == hash_token(token)
    assert len(digest) == 64
    assert int(digest, 16) >= 0  # valid hex


def test_hash_differs_for_different_tokens() -> None:
    assert hash_token("tkm_a") != hash_token("tkm_b")
