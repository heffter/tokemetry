# Token rotation runbook

Rotating API bearer tokens without downtime (FR-SEC-009). Tokens are
scoped and revocable; the plaintext is shown only once at creation, and
revocation never deletes history.

## When to rotate

- On a suspected leak (rotate immediately, see below).
- On a schedule (e.g. quarterly) for long-lived collector/proxy tokens.
- When narrowing scopes (replace a broad token with a least-privilege one).

## Zero-downtime replacement flow

1. **Mint the replacement** with the minimum scopes the client needs:
   `POST /api/v1/tokens` (scope `admin:tokens`) or the dashboard Settings view.
   Give it a clear label (e.g. `proxy-2026q3`). Capture the one-time secret.
2. **Deploy the new secret** to the client (collector/proxy config or secret
   manager) and restart/reload it. Both the old and new tokens are valid now,
   so ingest never stops.
3. **Confirm the new token is in use**: watch `last_used` advance on the new
   token (`GET /api/v1/tokens`) and the source keep ingesting.
4. **Revoke the old token**: `DELETE /api/v1/tokens/{old-label}`. Revocation is
   a soft flag -- its history is retained, and both the create and the revoke
   are recorded in the audit log (`token_create` / `token_revoke`).
5. **Verify** the old token is refused (`401`) and the new one still works.

## Suspected leak (fast path)

1. Revoke the compromised token first: `DELETE /api/v1/tokens/{label}` -- this
   takes effect immediately; in-flight requests with it start failing `401`.
2. Mint a replacement and deploy it (steps 1-3 above). Ingest resumes once the
   client has the new secret.
3. Review the audit log (`GET /api/v2/admin/audit`) for actions taken with the
   leaked token's label, and the source's recent ingest, to scope the impact.

## Notes

- The **bootstrap token** (`TOKEMETRY_API_BOOTSTRAP_TOKEN`) is for
  administration only; rotate it by changing the env var and restarting, and
  keep it off day-to-day clients (mint scoped tokens instead).
- Bearer tokens must only travel over TLS/WireGuard -- never plaintext on a
  public interface.
