SafeAgent

Deterministic execution guard for AI agents.

Install

pip install safeagent-exec-guard

SafeAgent prevents duplicate, replayed, or premature irreversible actions triggered by LLM-based agents.

It enforces:

request-id (nonce) deduplication

deterministic state transitions

exactly-once execution semantics

durable state persistence (SQLite)

This repository demonstrates a control-plane pattern for safe AI agent execution.

INSTALL

pip install safeagent-exec-guard

Requires Python 3.10+

EXACTLY-ONCE TOOL EXECUTION

Example:

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

If the same request_id is replayed, SafeAgent returns the original receipt instead of executing the side effect again.

WHY SAFEAGENT

AI agents frequently retry tool calls when:

APIs time out

orchestration layers restart

network calls fail

workflows replay events

Without protection this causes duplicate actions such as:

duplicate emails

duplicate payouts

duplicate tickets

duplicate trades

SafeAgent sits between the agent decision and the irreversible action.

WITHOUT SAFEAGENT

create_support_ticket(customer_id="C123")
create_support_ticket(customer_id="C123")

duplicate ticket created

WITH SAFEAGENT

from safeagent_exec_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def create_support_ticket(payload):
    print("CREATING TICKET for", payload["customer_id"])

receipt = registry.execute(
    request_id="agent_action_123",
    action="create_support_ticket",
    payload={"customer_id": "C123"},
    execute_fn=create_support_ticket,
)

print(receipt)

Replaying the same request_id returns the same receipt.

OPENAI STYLE TOOL EXAMPLE

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

Example output:

FIRST CALL
REAL SIDE EFFECT: sending email to user123@example.com

SECOND CALL WITH SAME request_id
dedup_same_request_id
same execution_id returned

LANGCHAIN STYLE TOOL EXAMPLE

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

SafeAgent ensures retries do not execute the side effect twice.

CREWAI STYLE TOOL EXAMPLE

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

CrewAI agents can retry actions safely because SafeAgent deduplicates execution.

WHAT PROBLEM DOES THIS SOLVE

Production AI agents frequently:

retry tool calls

replay webhook events

loop under uncertainty

trigger the same action twice

When those actions touch real systems duplicates are expensive.

Examples:

sending emails twice

charging customers twice

placing duplicate trades

creating duplicate tickets

SafeAgent ensures irreversible actions run only once.

HIGH LEVEL FLOW

Agent Decision
→ Reconciliation
→ Finality Gate
→ Execution
→ Receipt

STATE MACHINE

OPEN
→ RESOLVED_PROVISIONAL
→ IN_RECONCILIATION
→ FINAL
→ SETTLED

Properties

ambiguous signals enter reconciliation

execution only allowed in FINAL

replay safe execution

late signals ignored after finality

AGENT RETRY DEMO

Simulate an AI agent retrying a payment tool call:

python examples/agent_retry_demo.py

DEMOS

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

PROJECT STRUCTURE

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

LICENSE

Apache 2.0