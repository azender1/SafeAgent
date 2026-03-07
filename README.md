
# SafeAgent

Deterministic execution guard for AI agents.

SafeAgent prevents duplicate, replayed, or premature irreversible actions triggered by LLM-based agents.

It enforces:

- request-id (nonce) deduplication
- deterministic state transitions
- exactly-once execution semantics
- durable state persistence (SQLite)

SafeAgent sits between an agent decision and the irreversible side effect.

Examples include preventing duplicate:

- emails
- payments
- tickets
- trades

---

# Install

pip install safeagent-exec-guard

Requires Python 3.10+

---

# Exactly-once Tool Execution

```python
from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send_email(payload):
    print("SENDING EMAIL to", payload["to"])

receipt = registry.execute(
    request_id="email:C123:invoice",
    action="send_email",
    payload={"to": "c123@example.com"},
    execute_fn=send_email,
)

print(receipt)
```

If the same `request_id` is replayed, SafeAgent returns the original receipt instead of executing the side effect again.

---

# Why SafeAgent

AI agents frequently retry tool calls when:

- APIs time out
- orchestration layers restart
- network calls fail
- workflows replay events

Without protection this causes duplicate actions such as:

- duplicate emails
- duplicate payouts
- duplicate tickets
- duplicate trades

SafeAgent ensures irreversible actions run **exactly once**.

---

# OpenAI-style Tool Example

```python
from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send_email(payload):
    print("REAL SIDE EFFECT: sending email to", payload["to"])

receipt = registry.execute(
    request_id="email:user123:invoice",
    action="send_email",
    payload={
        "to": "user123@example.com",
        "template": "invoice_reminder",
    },
    execute_fn=send_email,
)

print(receipt)
```

Example output:

FIRST CALL
REAL SIDE EFFECT: sending email to user123@example.com

SECOND CALL WITH SAME request_id
dedup_same_request_id
same execution_id returned

---

# LangChain-style Tool Example

```python
from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send_email(payload):
    print("REAL SIDE EFFECT: LangChain email to", payload["to"])
    return {"status": "sent", "to": payload["to"]}

def safe_langchain_tool(request_id, payload):
    return registry.execute(
        request_id=request_id,
        action="send_email",
        payload=payload,
        execute_fn=send_email,
    )

print(safe_langchain_tool("langchain_email_1", {"to": "user@example.com"}))
print(safe_langchain_tool("langchain_email_1", {"to": "user@example.com"}))
```

---

# CrewAI-style Tool Example

```python
from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def crew_send_email(payload):
    print("REAL SIDE EFFECT: CrewAI email to", payload["to"])
    return {"status": "sent", "to": payload["to"]}

def crew_safe_action(request_id, payload):
    return registry.execute(
        request_id=request_id,
        action="send_email",
        payload=payload,
        execute_fn=crew_send_email,
    )

print(crew_safe_action("crew_email_1", {"to": "crew@example.com"}))
print(crew_safe_action("crew_email_1", {"to": "crew@example.com"}))
```

---

# Agent Retry Demo

Simulate an AI agent retrying a payment action:

python examples/agent_retry_demo.py

The customer is charged only once even if the agent retries.

---

# State Machine

SafeAgent enforces deterministic finality:

OPEN  
→ RESOLVED_PROVISIONAL  
→ IN_RECONCILIATION  
→ FINAL  
→ SETTLED  

Properties:

- ambiguous signals enter reconciliation
- execution allowed only in FINAL
- replay-safe execution
- late signals ignored after finality

---

# Demos

Duplicate Execution Prevention

python examples/safe_agent_demo.py

AI Outcome Simulation

python examples/simulate_ai.py

Persistence Demo

python examples/persist_demo.py

OpenAI Tool Example

python examples/openai_tool_safeagent.py

LangChain Example

python examples/langchain_safeagent.py

CrewAI Example

python examples/crewai_safeagent.py

---

# Project Structure

models.py  
state_machine.py  
reconciliation.py  
gate.py  
store.py  
policy.py  

settlement_requests.py  

examples/  
safe_agent_demo.py  
simulate_ai.py  
persist_demo.py  
nonce_demo.py  
openai_tool_safeagent.py  
langchain_safeagent.py  
crewai_safeagent.py  

---

# License

Apache 2.0
