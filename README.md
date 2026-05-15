<!-- mcp-name: io.github.azender1/safeagent -->
# SafeAgent — Execution Guard for AI Agents

AI agents retry. Retries fire side effects twice.

Duplicate payment. Duplicate email. Duplicate trade. Duplicate ticket.

SafeAgent is an execution guard that sits between an agent decision and an irreversible action. It gives every tool call a stable claim before execution, records a durable receipt on first run, and returns that receipt on every retry — without running the side effect again.

Python 3.10+ · Apache-2.0 · [Live demo](https://azender1.github.io/SafeAgent)

---

## What it is

| Component | Install | Description |
|-----------|---------|-------------|
| **Python library** | `pip install safeagent-exec-guard` | Decorator and registry for exactly-once tool execution |
| **n8n community node** | `npm install n8n-nodes-safeagent` | Drop-in guard for any n8n workflow |
| **x402-gated claim service** | [Orbis listing](https://orbisapi.com/proxy/safeagent-execution-guard-bb0b02) | Pay-per-call API, autonomously discoverable by AI agents on Base |

---

## The problem

```
agent calls tool
    ↓
network timeout
    ↓
agent retries
    ↓
side effect runs twice
```

Most agent frameworks handle retries at the transport layer. None of them know whether the side effect already happened. SafeAgent does.

The failure is worse than it looks. When LangGraph Cloud sweeps a run as stale and re-dispatches it, the new worker starts fresh — any in-memory dedup guard is gone. The claim has to live in durable storage outside the process, keyed before execution starts.

---

## Layer 2 in the agent execution stack

```
DashClaw      — attribution + approval         (decision_id)
    └── SafeAgent  — exactly-once execution guard   (request_id)
            └── Mycelium Trails — on-chain receipt      (action_ref → Base/Arbitrum)
```

Joint interface spec: [giskard09/argentum-core#7](https://github.com/giskard09/argentum-core/issues/7)

Canonical key derivation:
```
action_ref = SHA-256(agent_id || action_type || scope || timestamp_ms)
```
All four fields required. `timestamp_ms` encoded as int64, 8 bytes, big-endian.

---

## Quickstart — Python

```python
from settlement.settlement_requests import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send_invoice():
    print("Sending invoice...")

# First call — executes the side effect
receipt = registry.execute(
    request_id="invoice:C123",
    action="send_invoice",
    payload={"to": "c123@example.com"},
    execute_fn=send_invoice,
)

# Retry with the same request_id — returns the original receipt, no second send
receipt = registry.execute(
    request_id="invoice:C123",
    action="send_invoice",
    payload={"to": "c123@example.com"},
    execute_fn=send_invoice,
)
```

Same `request_id` → original receipt returned → side effect runs exactly once.

---

## Quickstart — n8n

Install the community node:

```
n8n-nodes-safeagent
```

Build a workflow:

```
[Webhook] → [SafeAgent Guard (Claim)] → Proceed → [Your Action] → [SafeAgent Guard (Settle)]
                                      → Skip   → [No Operation]
```

Webhook fires twice → action runs once.

---

## x402 Claim Service

SafeAgent runs as a live payment-gated claim server on Base mainnet. Any x402-enabled agent can call it autonomously — no signup, no API key.

```
POST https://orbisapi.com/proxy/safeagent-execution-guard-bb0b02/claim
```

- `$0.001 USDC` per claim
- Returns `PROCEED` (new claim) or `SKIP` (already ran)
- Indexed on [Bazaar](https://bazaar-validate.vercel.app/) and [agentic.market](https://agentic.market)
- Validated on Base and Solana

Configure x402 version for your facilitator:
```bash
SAFEAGENT_X402_VERSION=2  # Orbis proxy (default)
SAFEAGENT_X402_VERSION=1  # CDP facilitator direct
```

---

## Claude Desktop (MCP)

Use SafeAgent tools directly in Claude Desktop — no code required.

**Install dependencies:**
```bash
pip install mcp httpx
```

**Add to your Claude Desktop config:**

Windows: `%APPDATA%\Claude\claude_desktop_config.json`
Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "safeagent": {
      "command": "python",
      "args": ["C:\\path\\to\\safeagent_exec_guard\\mcp_server.py"],
      "cwd": "C:\\path\\to\\SAFEAGENT"
    }
  }
}
```

Restart Claude Desktop. SafeAgent tools appear in the **+** menu. Claude can now call `safeagent_claim` and `safeagent_settle` directly in any conversation.

---

## Two-phase execution receipts

A crash mid-call leaves `PENDING`, not a false `COMMITTED`. The sweeper resets stuck claims automatically.

```
CLAIMABLE → PENDING → COMMITTED
                ↑
            sweeper resets after timeout
```

```
POST /claim         — returns PROCEED or SKIP ($0.001 USDC)
POST /settle/{id}   — marks COMMITTED after success (free)
GET  /audit         — claim history by agent wallet (free)
GET  /health        — server status (free)
```

---

## How it works

Every execution goes through a four-step control plane:

```
Agent decision
    → Request-ID dedup   (has this exact call run before?)
    → Execute once       (run the side effect)
    → Receipt stored     (durable, survives restarts)
    → Settle             (transition PENDING → COMMITTED)
```

---

## Works with any agent framework

- **OpenAI** tool calls
- **LangChain** / **LangGraph** tools
- **CrewAI** actions
- **Claude / MCP** tool execution
- **n8n** workflows
- Any Python function that touches a real system

---

## Key properties

- **Exactly-once execution** — same `request_id` never fires twice
- **Durable receipts** — SQLite-backed, survives process restarts
- **Crash-safe** — two-phase PENDING → COMMITTED, no false receipts
- **Audit trail** — every execution recorded with payload, outcome, and agent wallet
- **Agent-native** — x402 payment-gated, Bazaar-indexed, autonomously discoverable

---

## Execution Guard RFC

The four-layer execution safety model is documented in [RFC_EXECUTION_GUARD.md](RFC_EXECUTION_GUARD.md):

```
Layer 1: Intent nonce       — caller-derived stable ID
Layer 2: Execution guard    — insert-if-not-exists at side-effect boundary  ← SafeAgent
Layer 3: Correlation ID     — shared ID across composed tool boundaries
Layer 4: Execution receipt  — post-execution proof, anchored externally
```

---

## Run the demos

```bash
# Duplicate execution prevention
python examples/safe_agent_demo.py

# Stochastic agent signal simulation
python examples/simulate_ai.py

# Restart safety (run twice)
python examples/persist_demo.py
python examples/persist_demo.py
```

---

## License

Apache-2.0
