# Security and privacy test suite (Task 70.8)

Adversarial coverage of the API surface, mapped to the PRD 18.6 security-test
requirements. Runs in the standard CI gate (`pytest tests/security`).

| Requirement (PRD 18.6 / FR) | Test |
|---|---|
| Prohibited content keys across metadata extension points (FR-PRIV-012) | `test_content_and_limits.py::test_prohibited_key_in_extra_rejected`, `::test_prohibited_key_in_dimensions_rejected` |
| Oversized metadata (NFR-SEC-004) | `test_content_and_limits.py::test_oversized_request_rejected` |
| Deeply nested JSON (NFR-SEC-004) | `test_content_and_limits.py::test_deeply_nested_extra_rejected` |
| Token scope bypass (each scope vs endpoint class) | `test_scope_and_token_bypass.py::test_query_token_cannot_ingest`, `::test_ingest_token_cannot_reach_admin`, `::test_query_token_cannot_reach_admin` |
| Forged / truncated tokens | `test_scope_and_token_bypass.py::test_forged_token_is_unauthorized`, `::test_truncated_bootstrap_token_is_unauthorized`, `::test_no_token_is_unauthorized` |
| Revoked token (REST) | `test_revoked_token.py::test_revoked_token_refused_on_rest` |
| Revoked token in-flight WebSocket disconnection (NFR-SEC-008) | `test_revoked_token.py::test_revoked_token_disconnected_in_flight` |
| WebSocket authorization edge cases | `test_revoked_token.py::test_revoked_token_refused_at_ws_connect` |
| Secret redaction in API responses (FR-PRIV-011) | `test_redaction_and_pseudonymity.py::test_minted_token_secret_not_echoed_in_audit`, `::test_token_list_never_returns_secrets` |
| Pseudonymized identifiers flow end to end (FR-PRIV-004) | `test_redaction_and_pseudonymity.py::test_pseudonymized_identifiers_flow_through_opaquely` |
| SQL and filter injection through query parameters | `test_injection.py::test_injection_in_filter_matches_nothing_and_preserves_data`, `::test_injection_in_group_by_is_rejected_not_executed` |

Related coverage lives in the integration suite and is not duplicated here:
`tests/unit/test_privacy.py` (validator internals, the 62.2 fuzz corpus),
`tests/integration/test_scope_enforcement.py` (scope matrix across v1/v2/WS),
`tests/integration/test_hardening_api.py` (rate limits, CORS, secure headers),
and `tests/integration/test_audit_api.py` (token-audit redaction).

Stored fields are catalogued for privacy review in
[docs/architecture/stored-fields.md](../../../../docs/architecture/stored-fields.md).
