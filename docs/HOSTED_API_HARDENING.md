# Hosted API hardening rollout (review branch)

This branch proposes a breaking change to the hosted `/claim`, `/claim/test`,
`/settle/{request_id}`, and `/audit` contracts. It is **not deployed**. Set up
the service, callers, and dashboard together before merging or deploying.

## Server configuration

- Configure `SAFEAGENT_SETTLEMENT_SECRET` with at least 32 random characters.
  Keep it stable and private. It derives a per-request settlement capability;
  changing it invalidates all outstanding capabilities, including those for
  PENDING claims. Reconcile existing PENDING rows before rotation.
- Configure a different `SAFEAGENT_AUDIT_TOKEN` with at least 32 random
  characters. Send it only from trusted backend services, never browser code.
  `/audit` returns 503 without configuration and 403 without the token.
- Configure both secrets before deploying code. The paid claim middleware
  rejects missing settlement configuration before payment verification so a
  broken deployment cannot charge for a claim it cannot settle.
- Review who can access audit records, including the public dashboard and
  CrewAI example. The dashboard needs a server-side authenticated proxy or a
  redacted public data source; the browser must not receive the admin token.

## Client flow

1. Submit `/claim` or `/claim/test`. `PROCEED` includes `request_id` and
   `settlement_token`. Persist both durably with the logical action before
   performing the external effect; treat the token as a secret.
2. After verifying the provider's authoritative outcome, send
   `X-SafeAgent-Settlement-Token: <token>` on `POST /settle/{request_id}` with
   `{"result": {...}}`. A missing or invalid token returns 403.
3. Duplicate `PENDING` remains unresolved. A duplicate `SKIP` returns the
   status without `existing` unless the request carries the same capability
   header. Keep the token to retrieve the committed result on a later retry.
   There is no self-service token recovery from a public request ID.
4. Trusted audit clients send `X-SafeAgent-Audit-Token: <admin token>` to
   `GET /audit`. A browser must never embed either secret.

For historical PENDING rows created before this rollout, derive/assign a
capability only through a controlled migration after verifying ownership and
the provider outcome. Never mark them COMMITTED solely because they are old.

`PaymentClient` supports the settlement token header. The MCP demo now uses
the free test claim schema and requires the returned derived request ID and
capability for settlement. The CrewAI cache example still uses an obsolete
hosted request shape and audit lookup; do not use it against this deployment
until it is migrated and independently tested. It now raises on API failures
instead of allowing a side effect after an uncertain claim.

## Release checks

- Run `pytest -q tests/test_hosted_api_access.py tests/test_hosted_claim_test_pending.py tests/test_payment_server.py tests/test_crewai_backend_failure.py`.
- Test the deployed app and dashboard with a safe synthetic claim, then a
  duplicate, a valid settlement, and an unauthorized settlement attempt.
- Verify no capabilities or audit tokens enter request logs, analytics,
  browser bundles, or public issue comments.

This capability only controls who can report settlement to SafeAgent. A
caller-provided result is not provider proof. SafeAgent Control still requires
an independent authoritative readback before claiming an external outcome.
