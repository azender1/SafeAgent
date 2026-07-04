# n8n-nodes-safeagent

**SafeAgent Execution Guard for n8n** - exactly-once execution for any workflow that touches payments, emails, trades, or webhooks.

Gives every workflow item a durable claim before a side-effectful action runs, then routes to PROCEED (new) or SKIP (duplicate already seen). Prevents double-sends, double-charges, and double-trades when agents or webhooks retry.

Cited as a normative requirement in the A2A v0.4 RFC #1920 (https://github.com/a2aproject/A2A/discussions/1920) - part of a four-implementation, byte-verifiable execution safety stack, 11/11 cross-implementation conformance vectors byte-identical.

---

## Installation

In your n8n instance go to Settings -> Community Nodes -> Install and enter:

n8n-nodes-safeagent

Or install manually:

npm install n8n-nodes-safeagent

---

## How it works

State machine: PENDING -> COMMITTED | SKIP

Before any irreversible action - a Stripe charge, an outbound email, a trade, a webhook handler - the node claims a (Request ID, Action) pair in durable storage:

PROCEED - New claim, first time seeing this key. Run your action, then call Settle.
SKIP - Already COMMITTED, this action already ran. Return the cached result, do not re-execute.

If the workflow crashes between Claim and Settle, the claim stays PENDING. The next run with the same Request ID will safely re-attempt.

---

## Operations

### Claim

Atomically reserves a (Request ID, Action) pair before execution. Returns PROCEED on first call, SKIP on any repeat with the same key.

### Settle

Marks a previously claimed pair as COMMITTED once the action has completed successfully. Call this at the end of your Proceed branch.

---

## Quick test

Build a workflow with three nodes:

[Manual Trigger] -> [SafeAgent Guard (Claim)] -> PROCEED -> [your action] -> [SafeAgent Guard (Settle)]
                                              -> SKIP    -> [No Operation]

1. Set Request ID to a fixed value, e.g. test-001.
2. Set Action to a label, e.g. send_email.
3. Execute the workflow - item exits PROCEED.
4. Execute again with the same Request ID - item exits SKIP.

---

## Node parameters

Operation - claim or settle - default: claim
Request ID - Unique idempotency key (e.g. webhook event ID, message UUID)
Action - Short label for the action being guarded (e.g. send_email, payment.send)
Database Path - Path to the local SQLite file (relative to n8n working directory) - default: safeagent.db

---

## Output fields

Claim -> PROCEED:
{ "requestId": "evt-abc123", "action": "send_email", "status": "PROCEED" }

Claim -> SKIP:
{ "requestId": "evt-abc123", "action": "send_email", "status": "SKIP", "existing": {} }

Settle:
{ "requestId": "evt-abc123", "action": "send_email", "status": "COMMITTED" }

---

## Common use cases

Stripe node times out, n8n retries -> Customer charged twice -> With SafeAgent: SKIP on second call
Webhook delivered twice (Stripe/GitHub/Twilio at-least-once) -> Event processed twice -> With SafeAgent: SKIP on second call
Email node retried after transient error -> Duplicate email sent -> With SafeAgent: SKIP on second call
AI agent tool call retried after crash -> Duplicate side effect -> With SafeAgent: SKIP on second call

For webhook deduplication, use the provider event ID (e.g. Stripe event.id) as the Request ID - it is stable across retries.

---

## Also available as

- Python library - pip install safeagent-exec-guard
- x402 pay-per-call API - $0.001 USDC per claim, Base or Solana, no signup - https://safeagent-production.up.railway.app/claim
- Claude Desktop MCP - safeagent_claim and safeagent_settle tools
- MCP Registry - io.github.azender1/safeagent

---

## Links

- GitHub: https://github.com/azender1/SafeAgent
- Conformance fixtures: https://github.com/azender1/SafeAgent/tree/main/docs/conformance

---

## License

Apache-2.0
