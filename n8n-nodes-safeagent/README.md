# n8n-nodes-safeagent

n8n community node — **SafeAgent Execution Guard**

Gives every workflow item a durable claim before a side-effectful action runs,
then routes to **Proceed** (new) or **Skip** (duplicate already seen).
Prevents double-sends, double-charges, and double-trades when agents or
webhooks retry.

[![npm](https://img.shields.io/npm/v/n8n-nodes-safeagent)](https://www.npmjs.com/package/n8n-nodes-safeagent)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

---

## Installation

In your n8n instance go to **Settings → Community Nodes → Install** and enter:

```
n8n-nodes-safeagent
```

Or install manually:

```bash
npm install n8n-nodes-safeagent
```
## Also available as

- **x402 pay-per-call API** — [Orbis listing](https://orbisapi.com/proxy/safeagent-execution-guard-bb0b02) — $0.001 USDC per claim, no signup, autonomously discoverable by AI agents on Base
- **Python library** — `pip install safeagent-exec-guard`
- **Claude Desktop MCP** — `safeagent_claim` and `safeagent_settle` tools available directly in Claude Desktop
- **MCP Registry** — `io.github.azender1/safeagent`
- **Bazaar indexed** — discoverable by any x402-enabled agent
---

## Also available as

- **x402 pay-per-call API** — [Orbis listing](https://orbisapi.com/proxy/safeagent-execution-guard-bb0b02) — $0.001 USDC per claim, no signup, autonomously discoverable by AI agents on Base
- **Python library** — `pip install safeagent-exec-guard`
- **Claude Desktop MCP** — `safeagent_claim` and `safeagent_settle` tools available directly in Claude Desktop
- **MCP Registry** — `io.github.azender1/safeagent`
- **Bazaar indexed** — discoverable by any x402-enabled agent

---

## Operations

### Claim

Atomically reserves a `(Request ID, Action)` pair in a local SQLite database.

| Output | When |
|--------|------|
| **Proceed** | Pair is new — run your action |
| **Skip** | Pair already seen — skip the action |

### Settle

Marks a previously claimed pair as `SETTLED` once the action has completed
successfully. Call this at the end of your Proceed branch.

---

## Quick test

Build a workflow with three nodes:

```
[Manual Trigger] → [SafeAgent Guard (Claim)] → Proceed → [SafeAgent Guard (Settle)]
                                              → Skip   → [No Operation]
```

1. Set **Request ID** to a fixed value, e.g. `test-001`.
2. Set **Action** to `send_email` (or any label).
3. Execute the workflow.
   - First run: item exits **Proceed**.
   - Second run with the same Request ID: item exits **Skip**.

---

## Node parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| Operation | `claim` or `settle` | `claim` |
| Request ID | Unique idempotency key (e.g. webhook event ID, message UUID) | — |
| Action | Short label for the action being guarded (e.g. `send_email`) | — |
| Database Path | Path to the SQLite file (relative to n8n working directory) | `safeagent.db` |

---

## Output fields

```json
{
  "requestId": "evt-abc123",
  "action": "send_email",
  "inserted": true,
  "status": "OPEN"
}
```

For **Settle**:

```json
{
  "requestId": "evt-abc123",
  "action": "send_email",
  "settled": true,
  "status": "SETTLED"
}
```

---

## License

Apache-2.0
