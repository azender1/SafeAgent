# SafeAgent

[![PyPI version](https://img.shields.io/pypi/v/safeagent-exec-guard.svg)](https://pypi.org/project/safeagent-exec-guard/)
[![Python versions](https://img.shields.io/pypi/pyversions/safeagent-exec-guard.svg)](https://pypi.org/project/safeagent-exec-guard/)
[![License](https://img.shields.io/pypi/l/safeagent-exec-guard.svg)](https://github.com/azender1/SafeAgent/blob/main/LICENSE)

Exactly-once execution guard for AI agent side effects.

SafeAgent prevents duplicate, replayed, or premature irreversible actions triggered by LLM agents or distributed workflows.

Typical protected actions include:

- payments
- emails / notifications
- tickets
- trades
- tournament payouts
- financial settlement

---

# Installation

```bash
pip install safeagent-exec-guard
```

Python **3.10+ required**.

---

# Why SafeAgent Exists

AI systems retry operations constantly.

Examples:

- agent loops retry tool calls
- HTTP clients retry failed requests
- queue workers replay jobs
- orchestrators restart workflows

Without protection this can cause:

```text
retry -> duplicate payment
retry -> duplicate email
retry -> duplicate ticket
retry -> duplicate payout
```

SafeAgent inserts an execution guard between the decision and the irreversible side effect.

```text
agent decision
↓
request_id generated
↓
SafeAgent execution guard
↓
side effect executes once
↓
future retries return cached receipt
```

---

# Minimal Example

```python
from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send_email(payload):
    print("SENDING EMAIL:", payload["to"])

receipt = registry.execute(
    request_id="email:C123:invoice",
    action="send_email",
    payload={"to": "c123@example.com"},
    execute_fn=send_email,
)

print(receipt)
```

If the same `request_id` runs again, the side effect is **not executed again**.  
SafeAgent returns the stored receipt.

---

# Decorator API

SafeAgent can wrap side-effecting functions directly.

```python
from safeagent_exec_guard import SettlementRequestRegistry, safeagent_guard

registry = SettlementRequestRegistry()

@safeagent_guard(
    registry=registry,
    action="send_email",
    request_id_fn=lambda payload: f"email:{payload['to']}:{payload.get('template','default')}",
)
def send_email(payload):
    print("REAL SIDE EFFECT:", payload["to"])

send_email({"to":"user@example.com","template":"invoice"})
send_email({"to":"user@example.com","template":"invoice"})
```

The second call returns the cached receipt instead of executing the side effect again.

---

# PeerPlay Tournament Settlement Demo

SafeAgent was extracted from a retry-safe settlement problem in **PeerPlay-style tournament settlement systems**.

When verification layers retry settlement, a payout could accidentally execute twice.

SafeAgent prevents duplicate payouts.

Run the demo:

```bash
python examples/peerplay_tournament_settlement_demo.py
```

What the demo shows:

1. tournament settlement executes
2. prize payout occurs
3. settlement retries
4. SafeAgent returns cached receipt
5. no duplicate payout occurs

---

# Framework Examples

### OpenAI-style tool execution

```bash
python examples/openai_tool_safeagent.py
```

### LangChain example

```bash
python examples/langchain_safeagent.py
```

### CrewAI example

```bash
python examples/crewai_safeagent.py
```

### Decorator example

```bash
python examples/decorator_safeagent.py
```

### LangChain adapter example

```bash
python examples/langchain_adapter_safeagent.py
```

---

# Failure Semantics

SafeAgent records a **durable execution receipt** for each `request_id`.

### Retry behavior

```text
same request_id → return stored receipt
```

The side effect is never executed again.

### Timeout after execution

```text
execution completed
response lost
caller retries
```

SafeAgent detects the existing receipt and returns it.

### Partial failures

SafeAgent **does not attempt automatic rollback**.

Applications should handle partial commits with:

- audit logs
- reconciliation processes
- compensating actions

SafeAgent guarantees **no duplicate execution**, not business policy validation.

---

# State Machine

```text
OPEN
→ RESOLVED_PROVISIONAL
→ IN_RECONCILIATION
→ FINAL
→ SETTLED
```

Properties:

- execution allowed only after finality
- retries return cached receipts
- ambiguous signals reconcile before execution

---

# Demo Scripts

```bash
python examples/safe_agent_demo.py
python examples/simulate_ai.py
python examples/persist_demo.py
python examples/openai_tool_safeagent.py
python examples/langchain_safeagent.py
python examples/crewai_safeagent.py
python examples/decorator_safeagent.py
python examples/langchain_adapter_safeagent.py
python examples/peerplay_tournament_settlement_demo.py
```

---

# Project Structure

```text
safeagent_exec_guard/
    settlement_requests.py
    store.py
    state_machine.py
    reconciliation.py
    decorators.py
    langchain.py

examples/
    safe_agent_demo.py
    simulate_ai.py
    persist_demo.py
    openai_tool_safeagent.py
    langchain_safeagent.py
    crewai_safeagent.py
    decorator_safeagent.py
    langchain_adapter_safeagent.py
    peerplay_tournament_settlement_demo.py
```

---

# License

Apache-2.0
