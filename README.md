# SafeAgent

Exactly-once execution guard for AI agent side effects.

SafeAgent prevents duplicate, replayed, or premature irreversible actions triggered by LLM-based agents.

It provides:

* request-id (nonce) deduplication
* deterministic state transitions
* exactly-once execution semantics
* durable state persistence with SQLite

SafeAgent sits between an agent decision and the irreversible side effect.

Typical protected actions include:

* emails
* payments
* tickets
* trades
* tournament payouts

## Install

```bash
pip install safeagent-exec-guard
```

Requires Python 3.10+.

## Why SafeAgent

AI agents frequently retry tool calls when:

* APIs time out
* orchestration layers restart
* network calls fail
* workflows replay events

Without protection, this can cause duplicate side effects such as repeated emails, payouts, tickets, or trades.

SafeAgent ensures irreversible actions run exactly once for a given `request\_id`.

## Exactly-once Tool Execution

```python
from safeagent\_exec\_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send\_email(payload):
    print("SENDING EMAIL to", payload\["to"])

receipt = registry.execute(
    request\_id="email:C123:invoice",
    action="send\_email",
    payload={"to": "c123@example.com"},
    execute\_fn=send\_email,
)

print(receipt)
```

If the same `request\_id` is replayed, SafeAgent returns the original receipt instead of executing the side effect again.

## PeerPlay Tournament Settlement Demo

SafeAgent was extracted from a retry-safe settlement problem in PeerPlay-style tournament payouts, where verification retries must not trigger duplicate prize releases or duplicate rake settlement.

Run the demo:

```bash
python examples/peerplay\_tournament\_settlement\_demo.py
```

What it shows:

* first settlement executes normally
* retry with the same `request\_id` returns a deduplicated receipt
* prize payout is released exactly once
* rake settlement is recorded exactly once

## OpenAI-style Tool Example

```python
from safeagent\_exec\_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send\_email(payload):
    print("REAL SIDE EFFECT: sending email to", payload\["to"])

receipt = registry.execute(
    request\_id="email:user123:invoice",
    action="send\_email",
    payload={
        "to": "user123@example.com",
        "template": "invoice\_reminder",
    },
    execute\_fn=send\_email,
)

print(receipt)
```

Example output:

```text
FIRST CALL
REAL SIDE EFFECT: sending email to user123@example.com

SECOND CALL WITH SAME request\_id
dedup\_same\_request\_id
same execution\_id returned
```

## LangChain-style Tool Example

```python
from safeagent\_exec\_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send\_email(payload):
    print("REAL SIDE EFFECT: LangChain email to", payload\["to"])
    return {"status": "sent", "to": payload\["to"]}

def safe\_langchain\_tool(request\_id, payload):
    return registry.execute(
        request\_id=request\_id,
        action="send\_email",
        payload=payload,
        execute\_fn=send\_email,
    )

print(safe\_langchain\_tool("langchain\_email\_1", {"to": "user@example.com"}))
print(safe\_langchain\_tool("langchain\_email\_1", {"to": "user@example.com"}))
```

SafeAgent ensures retries do not execute the side effect twice.

## CrewAI-style Tool Example

```python
from safeagent\_exec\_guard import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def crew\_send\_email(payload):
    print("REAL SIDE EFFECT: CrewAI email to", payload\["to"])
    return {"status": "sent", "to": payload\["to"]}

def crew\_safe\_action(request\_id, payload):
    return registry.execute(
        request\_id=request\_id,
        action="send\_email",
        payload=payload,
        execute\_fn=crew\_send\_email,
    )

print(crew\_safe\_action("crew\_email\_1", {"to": "crew@example.com"}))
print(crew\_safe\_action("crew\_email\_1", {"to": "crew@example.com"}))
```

CrewAI agents can retry actions safely because SafeAgent deduplicates execution.

## Failure Modes and Semantics

SafeAgent is designed to prevent duplicate execution of irreversible side effects by recording a durable execution receipt per `request\_id`.

### What happens on retry?

If the same `request\_id` is replayed, SafeAgent returns the existing receipt instead of executing the side effect again.

### Timeout after side effect executes

One important failure mode is:

* the side effect runs
* the response does not return
* the caller retries

SafeAgent treats the stored receipt as the source of truth. If a receipt already exists for that `request\_id`, the retry returns that receipt rather than executing again.

### Partial failure

If a tool partially commits work and then raises an error, SafeAgent does not attempt automatic rollback.

Instead, the failure should be handled explicitly by the application using:

* audit logs
* reconciliation logic
* downstream idempotency
* compensating actions where needed

### Retry after failure

Failure handling should be an explicit policy decision.

Common options include:

* return the stored failure receipt
* allow retry only for specific failure states
* require operator review for ambiguous outcomes

### Important design assumption

SafeAgent is strongest when:

* the `request\_id` is generated outside the LLM
* tool execution passes through a single guarded adapter layer
* downstream side effects are also idempotent when possible

SafeAgent is an execution guard, not a substitute for upstream business policy or workflow validation.

## Agent Retry Demo

Simulate an AI agent retrying a payment action:

```bash
python examples/agent\_retry\_demo.py
```

The customer is charged only once even if the agent retries.

## State Machine

SafeAgent enforces deterministic finality:

```text
OPEN
→ RESOLVED\_PROVISIONAL
→ IN\_RECONCILIATION
→ FINAL
→ SETTLED
```

Properties:

* ambiguous signals enter reconciliation
* execution allowed only in `FINAL`
* replay-safe execution
* late signals ignored after finality

## Demos

Duplicate execution prevention:

```bash
python examples/safe\_agent\_demo.py
```

AI outcome simulation:

```bash
python examples/simulate\_ai.py
```

Persistence demo:

```bash
python examples/persist\_demo.py
```

OpenAI tool example:

```bash
python examples/openai\_tool\_safeagent.py
```

PeerPlay tournament settlement demo:

```bash
python examples/peerplay\_tournament\_settlement\_demo.py
```

LangChain example:

```bash
python examples/langchain\_safeagent.py
```

CrewAI example:

```bash
python examples/crewai\_safeagent.py
```

## Project Structure

```text
models.py
state\_machine.py
reconciliation.py
gate.py
store.py
policy.py

settlement\_requests.py

examples/
    safe\_agent\_demo.py
    simulate\_ai.py
    persist\_demo.py
    nonce\_demo.py
    openai\_tool\_safeagent.py
    peerplay\_tournament\_settlement\_demo.py
    langchain\_safeagent.py
    crewai\_safeagent.py
```

## License

Apache-2.0

