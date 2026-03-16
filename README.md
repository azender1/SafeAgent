[![PyPI version](https://img.shields.io/pypi/v/safeagent-exec-guard.svg?cacheSeconds=300)](https://pypi.org/project/safeagent-exec-guard/)
[![Python versions](https://img.shields.io/pypi/pyversions/safeagent-exec-guard.svg)](https://pypi.org/project/safeagent-exec-guard/)
[![License](https://img.shields.io/pypi/l/safeagent-exec-guard.svg)](https://github.com/azender1/SafeAgent/blob/main/LICENSE)

Exactly-once execution guard for AI agent side effects.

## Demo

![SafeAgent Demo](https://raw.githubusercontent.com/azender1/SafeAgent/main/assets/safeagent-demo.gif)

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

pip install safeagent-exec-guard

Python 3.10+ required.

---

# Why SafeAgent Exists

AI systems retry operations constantly.

Examples:

- agent loops retry tool calls
- HTTP clients retry failed requests
- queue workers replay jobs
- orchestrators restart workflows

Without protection this can cause:

retry -> duplicate payment  
retry -> duplicate email  
retry -> duplicate ticket  
retry -> duplicate payout  

SafeAgent inserts an execution guard between the decision and the irreversible side effect.

agent decision  
↓  
request_id generated  
↓  
SafeAgent execution guard  
↓  
side effect executes once  
↓  
future retries return cached receipt  

---

# Minimal Example

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

If the same request_id runs again, the side effect is NOT executed again.  
SafeAgent returns the stored receipt.

---

# Decorator API

from safeagent_exec_guard import SettlementRequestRegistry, safeagent_guard

registry = SettlementRequestRegistry()

@safeagent_guard(
    registry=registry,
    action="send_email",
    request_id_fn=lambda payload: f"email:{payload['to']}:{payload.get('template','default')}",
)
def send_email(payload):
    print("REAL SIDE EFFECT:", payload["to"])

send_email({"to": "user@example.com", "template": "invoice"})
send_email({"to": "user@example.com", "template": "invoice"})

The second call returns the cached receipt instead of executing the side effect again.

---

# MCP Example

from safeagent_exec_guard import SettlementRequestRegistry
from safeagent_exec_guard.mcp import safe_mcp_tool

registry = SettlementRequestRegistry()

@safe_mcp_tool(
    registry=registry,
    action="send_payment",
    request_id_fn=lambda payload: f"payment:{payload['recipient']}:{payload['amount']}",
)
def send_payment(amount: float, recipient: str):
    print(f"REAL SIDE EFFECT: sending ${amount} to {recipient}")

Run the demo:

python examples/mcp_retry_demo.py

What it shows:

1. payment executes on first call
2. retry with the same logical action returns a cached receipt
3. the payment side effect runs exactly once

---

# Framework Examples

OpenAI-style tool execution

python examples/openai_tool_safeagent.py

LangChain example

python examples/langchain_safeagent.py

CrewAI example

python examples/crewai_safeagent.py

Decorator example

python examples/decorator_safeagent.py

LangChain adapter example

python examples/langchain_adapter_safeagent.py

MCP retry demo

python examples/mcp_retry_demo.py

---

# Failure Semantics

SafeAgent records a durable execution receipt for each request_id.

Retry behavior

same request_id -> return stored receipt

The side effect is never executed again.

Timeout after execution

execution completed  
response lost  
caller retries  

SafeAgent detects the existing receipt and returns it.

Partial failures

SafeAgent does NOT attempt automatic rollback.

Applications should handle partial commits with:

- audit logs
- reconciliation processes
- compensating actions

SafeAgent guarantees no duplicate execution, not business policy validation.

---

# License

Apache-2.0