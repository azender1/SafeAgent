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
  Administrative `/audit` access returns 503 without this configuration and
  403 for an invalid token; tenant audit access uses the tenant key below.
- Configure `SAFEAGENT_TENANT_KEYS` as a JSON object mapping stable tenant IDs
  to distinct, random API keys (32+ characters each), for example
  `{"customer-1":"<48-random-characters>"}`. Each paid caller sends its own
  `X-SafeAgent-Api-Key`. The server namespaces stored claim IDs by the tenant
  identity, so identical client request IDs cannot block each other. Keep
  tenant IDs stable when rotating keys; rotate each tenant key independently.
  Paid requests are rejected before x402 payment verification when tenant
  configuration or authentication is missing.
- The public `/claim/test` has its own `test:` ID namespace and cannot reserve
  a paid tenant claim. Do not use it for production work. `test:` is a reserved
  prefix for paid client IDs after rollout.
- `SAFEAGENT_CORS_ORIGINS` is an optional comma-separated allowlist for known
  browser origins. The default allows no cross-origin browser calls. Request
  validation errors no longer log raw bodies.
- Configure both secrets before deploying code. The paid claim middleware
  rejects missing settlement configuration before payment verification so a
  broken deployment cannot charge for a claim it cannot settle.
- Review who can access audit records, including the public dashboard and
  CrewAI example. The dashboard needs a server-side authenticated proxy or a
  redacted public data source; the browser must not receive the admin token.
- Update the Railway cron jobs for `/sweep` and `/sweep/anchor*` to send the
  audit token. Claim governance `/proof` and `/anchor` reads require tenant
  credentials for paid claims, the settlement capability for test claims, or
  the audit token for historical rows. An owner can publish a proof deliberately.

## Client flow

1. Submit `/claim` with the tenant key, or use `/claim/test` for a free demo.
   `PROCEED` includes `request_id` and
   `settlement_token`. Persist both durably with the logical action before
   performing the external effect; treat the token as a secret.
2. After verifying the provider's authoritative outcome, send the same tenant
   key and `X-SafeAgent-Settlement-Token: <token>` on
   `POST /settle/{request_id}` with `{"result": {...}}`. Test claims need only
   their settlement capability. A missing or invalid capability returns 403.
3. Duplicate `PENDING` remains unresolved. A duplicate `SKIP` returns the
   status without `existing` unless the request carries the same capability
   header. Keep the token to retrieve the committed result on a later retry.
   There is no self-service token recovery from a public request ID.
4. Tenant audit clients send their own API key to `GET /audit` and receive only
   tenant namespaced records. A privileged administrator can send
   `X-SafeAgent-Audit-Token: <admin token>` for global and historical audit.
   A browser must never embed any of these secrets.

Historical rows created before this rollout have unscoped IDs. The new tenant
route cannot reach them. Migrate them through a controlled reconciliation after
verifying tenant ownership and provider outcome; never mark PENDING rows
COMMITTED solely because they are old. Back up the database and plan the
readback/migration before release.

`PaymentClient` supports the tenant key and settlement token headers. The MCP demo now uses
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
