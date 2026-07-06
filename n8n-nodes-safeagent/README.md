# n8n-nodes-safeagent

**SafeAgent Execution Guard for n8n** - exactly-once execution for any workflow that touches payments, emails, trades, or webhooks.

Gives every workflow item a durable claim before a side-effectful action runs, then routes to PROCEED (new) or SKIP (duplicate already seen). Prevents double-sends, double-charges, and double-trades when agents or webhooks retry.

Cited as a normative requirement in the A2A v0.4 RFC #1920 (https://github.com/a2aproject/A2A/discussions/1920) - part of a four-implementation, byte-verifiable execution safety stack, 11/11 cross-implementation conformance vectors byte-identical.

---

## Free tier only - please read

This node talks HTTP to SafeAgent's hosted API and only calls the **free test endpoint**
(`POST /claim/test`), which is rate-limited to 10 calls per IP address, total, with no
payment required. It does not store anything locally (earlier versions used a local
SQLite file - that has been removed).

Why only the free tier: SafeAgent's paid, unlimited endpoint (`POST /claim`) is gated by
genuine on-chain x402 payment - each call needs a fresh EIP-3009-signed USDC authorization
from an EVM wallet, not a reusable API key. Bundling a wallet-signing library into this
package to support that would add a real runtime dependency, and n8n's Cloud verification
program does not allow verified community nodes to have any runtime dependencies. Keeping
this node free-tier-only and dependency-free keeps it eligible for verification and safe to
install.

For unlimited, paid, production usage, call SafeAgent's `POST /claim` endpoint directly
outside n8n - see the options under "Also available as" below.

---

## Installation

In your n8n instance go to Settings -> Community Nodes -> Install and enter:

n8n-nodes-safeagent

Or install manually:

npm install n8n-nodes-safeagent

---

## How it works

State machine: PENDING -> COMMITTED | SKIP

Before any irreversible action - a Stripe charge, an outbound email, a trade, a webhook handler - the node claims an (Agent ID, Action Type, Scope) triple against SafeAgent's hosted API:

PROCEED - New claim, first time seeing this key. Run your action, then call Settle.
SKIP - Already seen (COMMITTED or still PENDING). If COMMITTED, the cached result comes back in `existing` - do not re-execute.

If the workflow crashes between Claim and Settle, the claim stays PENDING. The next run with the same Agent ID / Action Type / Scope will safely re-attempt.

---

## Operations

### Claim

Calls `POST /claim/test` with your Agent ID, Action Type, and Scope, which SafeAgent combines server-side into a content-addressed request ID. Returns PROCEED on first call, SKIP on any repeat with the same combination.

### Settle

Calls `POST /settle/{request_id}` to mark a previously claimed request as committed, with its result. Call this at the end of your Proceed branch. Not rate-limited.

---

## Quick test

Build a workflow with three nodes:

[Manual Trigger] -> [SafeAgent Guard (Claim)] -> PROCEED -> [your action] -> [SafeAgent Guard (Settle)]
-> SKIP -> [No Operation]

1. Set Agent ID to a fixed value, e.g. my-agent.
2. Set Action Type to a label, e.g. send_email.
3. Set Scope to something unique per logical request, e.g. customer:123.
4. Execute the workflow - item exits PROCEED.
5. Execute again with the same Agent ID / Action Type / Scope - item exits SKIP.

---

## Node parameters

Operation - claim or settle - default: claim
Base URL - SafeAgent API base URL - default: https://safeagent-production.up.railway.app (override only for a self-hosted instance)

Claim:
Agent ID - identifier for the agent or workflow performing the action
Action Type - short label for the action being guarded (e.g. send_email, payment.send)
Scope - everything that makes this execution unique (e.g. customer ID, order ID, timestamp/bar)

Settle:
Request ID - the request_id returned by a previous Claim call
Result - arbitrary JSON to store against this claim once settled

---

## Output fields

Claim -> PROCEED:
{ "status": "PROCEED", "request_id": "...", "test": true, "calls_remaining": 9 }

Claim -> SKIP:
{ "status": "SKIP", "request_id": "...", "test": true, "calls_remaining": 8, "existing": {} }

Settle:
{ "status": "committed", "request_id": "..." }

---

## Common use cases

Stripe node times out, n8n retries -> Customer charged twice -> With SafeAgent: SKIP on second call
Webhook delivered twice (Stripe/GitHub/Twilio at-least-once) -> Event processed twice -> With SafeAgent: SKIP on second call
Email node retried after transient error -> Duplicate email sent -> With SafeAgent: SKIP on second call
AI agent tool call retried after crash -> Duplicate side effect -> With SafeAgent: SKIP on second call

For webhook deduplication, use the provider event ID (e.g. Stripe event.id) as the Scope - it is stable across retries.

Remember: this node's free tier is limited to 10 total calls per IP address. For real production volume, use one of the options below instead of this node.

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
