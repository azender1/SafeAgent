# SafeAgent

Exactly-once execution guard for AI agent side effects.

SafeAgent prevents duplicate, replayed, or premature irreversible actions triggered by LLM-based agents.

It provides:

- request-id (nonce) deduplication
- deterministic state transitions
- exactly-once execution semantics
- durable state persistence with SQLite

SafeAgent sits between an agent decision and the irreversible side effect.

Typical protected actions include:

- emails
- payments
- tickets
- trades

## Install

```bash
pip install safeagent-exec-guard
```

Requires Python 3.10+.

## Why SafeAgent

AI agents frequently retry tool calls when:

- APIs time out
- orchestration layers restart
- network calls fail
- workflows replay events

Without protection, this can cause duplicate side effects such as repeated emails, payouts, tickets, or trades.

SafeAgent ensures irreversible actions run exactly once for a given `request_id`.

## Exactly-once Tool Execution

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

## OpenAI-style Tool Example

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

```text
FIRST CALL
REAL SIDE EFFECT: sending email to user123@example.com

SECOND CALL WITH SAME request_id
dedup_same_request_id
same execution_id returned
```

## LangChain-style Tool Example

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

SafeAgent ensures retries do not execute the side effect twice.

## CrewAI-style Tool Example

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

CrewAI agents can retry actions safely because SafeAgent deduplicates execution.

## Agent Retry Demo

Simulate an AI agent retrying a payment action:

```bash
python examples/agent_retry_demo.py
```

The customer is charged only once even if the agent retries.

## State Machine

SafeAgent enforces deterministic finality:

```text
OPEN
→ RESOLVED_PROVISIONAL
→ IN_RECONCILIATION
→ FINAL
→ SETTLED
```

Properties:

- ambiguous signals enter reconciliation
- execution allowed only in `FINAL`
- replay-safe execution
- late signals ignored after finality

## PeerPlay Tournament Settlement Demo

SafeAgent was extracted from a retry-safety problem in PeerPlay-style tournament settlement, where verification retries must not trigger duplicate payouts or duplicate rake settlement.

Run the demo:

```bash
python examples/peerplay_tournament_settlement_demo.py

## Demos

Duplicate execution prevention:

```bash
python examples/safe_agent_demo.py
```

AI outcome simulation:

```bash
python examples/simulate_ai.py
```

Persistence demo:

```bash
python examples/persist_demo.py
```

OpenAI tool example:

```bash
python examples/openai_tool_safeagent.py
```

LangChain example:

```bash
python examples/langchain_safeagent.py
```

CrewAI example:

```bash
python examples/crewai_safeagent.py
```

## Project Structure

```text
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
```

## Failure Modes and Semantics

SafeAgent is designed to prevent duplicate execution of irreversible side effects by recording a durable execution receipt per `request_id`.

### What happens on retry?

If the same `request_id` is replayed, SafeAgent returns the existing receipt instead of executing the side effect again.

### Timeout after side effect executes

One important failure mode is:

- the side effect runs
- the response does not return
- the caller retries

SafeAgent treats the stored receipt as the source of truth. If a receipt already exists for that `request_id`, the retry returns that receipt rather than executing again.

### Partial failure

If a tool partially commits work and then raises an error, SafeAgent does not attempt automatic rollback.

Instead, the failure should be handled explicitly by the application using:

- audit logs
- reconciliation logic
- downstream idempotency
- compensating actions where needed

### Retry after failure

Failure handling should be an explicit policy decision.

Common options include:

- return the stored failure receipt
- allow retry only for specific failure states
- require operator review for ambiguous outcomes

### Important design assumption

SafeAgent is strongest when:

- the `request_id` is generated outside the LLM
- tool execution passes through a single guarded adapter layer
- downstream side effects are also idempotent when possible

In other words, SafeAgent is an execution guard, not a substitute for good downstream system design.

## License

Apache-2.0
