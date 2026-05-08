<!-- mcp-name: io.github.azender1/safeagent -->
# SafeAgent

AI agents retry. Retries fire side effects twice.

Duplicate payment. Duplicate email. Duplicate trade. Duplicate ticket.

SafeAgent is an execution guard that sits between an agent decision and an irreversible action. It gives every tool call a request ID, records a durable receipt on first execution, and returns that receipt on every retry — without running the side effect again.

Apache-2.0 · [Live demo](https://azender1.github.io/SafeAgent)

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

---

## Quickstart

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

## Works with any agent framework

- **OpenAI** tool calls
- **LangChain** tools
- **CrewAI** actions
- **Claude / MCP** tool execution
- Any Python function that touches a real system

---

## How it works

Every execution goes through a four-step control plane:

```
Agent decision
    → Finality gate      (is this outcome confirmed?)
    → Request-ID dedup   (has this exact call run before?)
    → Execute once       (run the side effect)
    → Receipt stored     (durable, survives restarts)
```

State machine: `OPEN → RESOLVED → IN_RECONCILIATION → FINAL → SETTLED`

Execution is only permitted from `FINAL`. Replays at any state return the stored receipt.

---

## Key properties

- **Exactly-once execution** — same request_id never fires twice
- **Durable receipts** — SQLite-backed, survives process restarts
- **Finality gating** — blocks execution on ambiguous agent signals
- **Confidence thresholds** — auto-finalizes when consensus exceeds threshold
- **Audit trail** — every execution recorded with payload and outcome

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

## Production use

SafeAgent is a reference implementation and pattern library. If you're deploying this in a production agent system, see `LICENSING.md` for commercial options.

---

## License

Apache-2.0
