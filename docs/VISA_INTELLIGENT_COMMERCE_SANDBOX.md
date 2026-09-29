# Visa / Cybersource Intelligent Commerce sandbox adapter

Status: **scaffolded for sandbox integration; no live or production transaction has been sent.**

This branch adds a provider-specific SafeAgent boundary around Cybersource Intelligent Commerce **Confirm Transaction Events**.

Provider test endpoint documented by Cybersource:

```
POST https://apitest.cybersource.com/acp/v1/instructions/{instructionId}/confirmations
```

SafeAgent behavior:

```
permit -> durable correlation -> provider dispatch
                         |
                         +-- response received -> provider observation recorded
                         |
                         +-- response lost -> PENDING_RECONCILIATION
                                               |
                                               +-- operator/provider readback
                                                   closes the state without
                                                   a second dispatch
```

## Security boundary

The SafeAgent action payload must never contain the Cybersource REST shared secret, key ID, MLE private key, .p12 contents, or passwords.

The credential-bearing HTTP/MLE implementation belongs in a transport object outside the agent-visible payload. The initial adapter therefore depends on a `CybersourceIntelligentCommerceTransport` protocol rather than embedding credentials in repository code.

## Why reconciliation is observation-only in this first cut

The current public Intelligent Commerce guide documents POST confirmation and its response, but this implementation does not assume an undocumented GET/readback endpoint for a lost confirmation response.

A lost provider response is therefore preserved as `PENDING_RECONCILIATION`. A separately obtained provider observation (for example from the sandbox dashboard or a documented readback added later) can be attached through `ingest_provider_observation()` without redispatching the consequence.

## Tomorrow's credentialed sandbox work

1. Keep REST Shared Secret and Response MLE material local.
2. Implement the authenticated/MLE transport using the Visa/Cybersource test host only.
3. Send one synthetic sandbox confirmation flow after the prerequisite Intelligent Commerce instruction exists.
4. Record the instruction ID, clientCorrelationId, SafeAgent permit ID, provider response, and retry behavior.
5. Inject a response-loss scenario only if it can be done without creating a second external consequence.
6. Do not enable `api.cybersource.com` production traffic.

This work is intentionally separate from the n8n package under review.


## GitHub Actions credential bridge

The repository now includes a manual-only workflow:

`.github/workflows/visa-sandbox-readiness.yml`

It performs **offline credential validation only**. It does not contact Visa or
Cybersource and does not submit a sandbox transaction.

Required GitHub Actions repository secrets:

- `VISA_SANDBOX_ORG_ID`
- `VISA_SANDBOX_KEY_ID`
- `VISA_SANDBOX_SHARED_SECRET`
- `VISA_SANDBOX_RESPONSE_MLE_P12_B64`
- `VISA_SANDBOX_RESPONSE_MLE_PASSWORD`
- `VISA_SANDBOX_RESPONSE_MLE_KEY_ID`
- `VISA_SANDBOX_REQUEST_MLE_CERT_B64`

The workflow validates:

1. shared-secret base64 encoding and local Visa JWT-v2 construction;
2. that the Response MLE P12 decrypts with the supplied password;
3. that the request-MLE public certificate parses and contains a public key.

It never prints any credential value.
