SafeAgent
Exactly-once execution for systems that retry
Your system will retry.  
SafeAgent makes sure it does not execute twice.
---
Why this exists
Retries are normal.
Timeouts happen. Connections drop. Workers restart. Agent frameworks retry tool calls. Queue consumers run again. Sometimes the first attempt already succeeded, but the result was lost or surfaced late.
That is how you get:
duplicate payments
duplicate trades
duplicate emails
duplicate orders
duplicate writes
inconsistent state
SafeAgent adds a durable execution guard in front of actions that must not run twice.
Even if your system runs it twice, it executes once.
---
Demo
MCP retry demo
![SafeAgent MCP Retry Demo](assets/mcp_demo.gif)
Postgres + Docker demo
![SafeAgent Postgres Demo](assets/postgres_demo.gif)
---
What SafeAgent does
SafeAgent uses a `request_id` to represent one logical action.
On the first attempt:
store the execution identity
allow the side effect to run
save the result
On later attempts with the same `request_id`:
detect that the action already exists
do not run the side effect again
reuse the original outcome or block re-execution
This makes retries safe for irreversible actions.
---
Install
```bash
pip install safeagent-exec-guard
```
---
Choose a backend
SafeAgent supports two practical modes:
SQLite for local, single-process, zero-config use
Postgres for distributed, multi-worker, production use
---
Quick start
1) Pick a request id
A `request_id` must identify one logical action.
Good examples:
payment id
order id
webhook delivery id
agent tool-call id
workflow step id
Bad examples:
current timestamp only
random id per retry
anything that changes across retries
---
2) Guard the side effect
This is the core pattern:
```python
if store.insert_if_not_exists(request_id, action):
    result = do_side_effect()
    store.complete(request_id, result)
else:
    print("Already executed.")
```
That is the entire idea.
---
SQLite example
Use SQLite when you want the simplest local setup.
```python
from settlement.settlement_requests import SettlementRequestRegistry

registry = SettlementRequestRegistry()

def send_email():
    print("REAL SIDE EFFECT: sending email")
    return {"status": "sent"}

request_id = "email_001"

print("FIRST ATTEMPT")
print(registry.execute(
    request_id=request_id,
    action="send_email",
    payload={"to": "user@example.com"},
    execute_fn=send_email,
))

print("\nRETRY")
print(registry.execute(
    request_id=request_id,
    action="send_email",
    payload={"to": "user@example.com"},
    execute_fn=send_email,
))
```
Expected behavior:
first call executes the side effect
second call does not run it again
---
Postgres example
Use Postgres when you want durable execution identity across processes, containers, or workers.
```python
import os
from safeagent_exec_guard.postgres_store import PostgresExecutionStore

dsn = os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")

store = PostgresExecutionStore(dsn)
store.init_db()

request_id = "payment_001"
action = "send_payment"

def send_payment():
    print("REAL SIDE EFFECT: sending payment")
    return {"status": "sent", "receipt_id": "rcpt_12345"}

print("FIRST ATTEMPT")
if store.insert_if_not_exists(request_id, action):
    result = send_payment()
    store.complete(request_id, result)
    print("DONE:", result)
else:
    print("Already executed.")

print("\nRETRY")
if store.insert_if_not_exists(request_id, action):
    result = send_payment()
    store.complete(request_id, result)
    print("DONE:", result)
else:
    print("Already executed.")
```
Expected behavior:
first attempt runs once
retry with the same `request_id` is blocked
---
Run the demo
Local Python
```bash
python examples/postgres_demo.py
```
Docker
Build:
```bash
docker build -t safeagent-demo .
```
Run:
```bash
docker run --rm -e POSTGRES_DSN="postgresql://postgres:postgres@host.docker.internal:5432/postgres" safeagent-demo
```
---
What problem this solves
SafeAgent is for irreversible side effects.
Examples:
payments
trades
outbound emails
order creation
webhooks
workflow triggers
external API mutations
agent tool calls that change state
SafeAgent is not primarily about caching, UI bugs, or speeding up code.  
It is about making execution safe when retries and failures happen.
---
How to think about it
SafeAgent is not a retry system.
SafeAgent is not observability.
SafeAgent is not insurance.
SafeAgent is an execution guarantee layer.
You already have retries. SafeAgent makes them safe.
---
Where it fits
Most systems already have some combination of:
framework retries
HTTP retries
queue retries
worker restarts
agent loop retries
Those layers are useful, but they can stack in dangerous ways.
SafeAgent sits in front of the side effect and answers one question:
Has this logical action already been allowed to execute?
If yes, do not run it again.
---
Why this matters for AI agents
Agent systems are especially vulnerable because retries can happen across multiple layers:
agent loop
tool caller
framework
transport
background worker
A failed tool call does not always mean the action did not happen.
Timeout does not mean it did not run.
That is where duplicate execution starts.
SafeAgent prevents that class of failure.
---
Backends
SQLite
Use when:
developing locally
running a single process
you want zero-config setup
Postgres
Use when:
running multiple workers
using containers
you need durable coordination
you want production-ready storage
---
Demos and framework examples
This repository includes examples for:
MCP-style flows
LangChain-style flows
CrewAI-style flows
Postgres-backed execution guard
Docker-based demo setup
The goal is the same in every case:
same request -> same result -> one execution
---
Mental model
Without SafeAgent:
system sends action
timeout or uncertain result
system retries
side effect runs again
With SafeAgent:
system sends action with `request_id`
execution identity is stored
side effect runs once
retry with same `request_id` is blocked
---
Minimal positioning
If you need to explain SafeAgent in one sentence:
SafeAgent stops systems from executing the same action twice when they retry.
---
License
MIT