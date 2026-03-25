# Execution Guard — Exactly-Once Execution for AI Agents

A missing layer for safe side effects in agent systems.

Retries can duplicate irreversible actions:
payments, emails, trades, and external API mutations.

Execution Guard ensures a side effect runs exactly once — even under retries.

> Even if your system runs it twice, it executes once.

---

## Quickstart (runs in ~10 seconds)

Run the same request twice — it executes once.

Install:

```bash
pip install safeagent-exec-guard
```

Minimal local example (SQLite):

```python
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

store = SQLiteExecutionStore("safeagent.db")
store.init_db()

def send_payment(request_id: str):
    action = "send_payment"

    if store.insert_if_not_exists(request_id, action):
        print("executing side effect")
        result = {"status": "sent", "receipt_id": "rcpt_12345"}
        store.complete(request_id, result)
        print("result:", result)
    else:
        print("duplicate blocked")

send_payment("req_123")
send_payment("req_123")
```

Expected output:

```text
executing side effect
result: {'status': 'sent', 'receipt_id': 'rcpt_12345'}
duplicate blocked
```

---

## Demo

A retry without protection can execute the same irreversible action twice.

Execution Guard guarantees the side effect runs once — even if the agent retries.

### Retry without protection → duplicate execution ❌
### Retry with Execution Guard → executes once and blocks duplicate ✅

![Before vs After](assets/before_after_demo.gif)

SafeAgent is a reference implementation of the Execution Guard pattern.

---

## Install

```bash
pip install safeagent-exec-guard
```

---

## Quick start (SQLite)

Use SQLite for local development, demos, and single-process workflows.

```python
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

store = SQLiteExecutionStore("safeagent.db")
store.init_db()

request_id = "payment_123"
action = "send_payment"

def do_side_effect():
    print("Executing payment...")
    return {"status": "sent", "receipt_id": "rcpt_12345"}

if store.insert_if_not_exists(request_id, action):
    result = do_side_effect()
    store.complete(request_id, result)
    print("Executed:", result)
else:
    print("Duplicate request detected — execution blocked")

```
See also: Wrap a side effect

## Postgres (production)

Use Postgres for distributed workers, production services, and multi-instance agent systems.

### Start Postgres with Docker

```bash
docker run --name safeagent-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=postgres \
  -p 5432:5432 \
  -d postgres:16
```

### Example

```python
import os
from safeagent_exec_guard.postgres_store import PostgresExecutionStore

dsn = os.getenv(
    "POSTGRES_DSN",
    "postgresql://postgres:postgres@localhost:5432/postgres"
)

store = PostgresExecutionStore(dsn)
store.init_db()

request_id = "payment_123"
action = "send_payment"

def do_side_effect():
    print("Executing payment...")
    return {"status": "sent", "receipt_id": "rcpt_12345"}

if store.insert_if_not_exists(request_id, action):
    result = do_side_effect()
    store.complete(request_id, result)
    print("Executed:", result)
else:
    print("Duplicate request detected — execution blocked")
```

### Reset the demo table

```bash
docker exec -it safeagent-postgres psql -U postgres -d postgres -c "TRUNCATE TABLE execution_requests;"
```

---

## How it works

```python
if store.insert_if_not_exists(request_id, action):
    result = do_side_effect()
    store.complete(request_id, result)
else:
    print("Duplicate request detected — execution blocked")
```

A request is identified once. Every retry resolves against that same execution record.

---

## Mental model

Without SafeAgent:

1. Request is sent.
2. Timeout or uncertainty happens.
3. The system retries.
4. The side effect runs again.

With SafeAgent:

1. Request is sent with a `request_id`.
2. Execution is recorded durably.
3. The side effect runs once.
4. Retries are blocked for the same request.

---

## Where this matters

- Payments
- Trading systems
- Background jobs
- Webhooks
- External API mutations
- AI agent tool calls
- Ticketing and messaging workflows

Any system where running the same side effect twice is unacceptable.

---

## Why this exists

Retries do not mean “nothing happened.”

They mean “we do not know what happened.”

Most systems retry anyway.

That is fine for reads.

It is dangerous for side effects.

That is how you get duplicate payments, duplicate emails, duplicate trades, and repeated external mutations.

Execution Guard adds a safety boundary at the side-effect layer:

- record the execution attempt
- execute once
- resolve retries safely

---

## Case studies

- [Trading Bot Case Study](docs/TRADING_BOT_CASE_STUDY.md)

## Backends

- SQLite → local / single process
- Postgres → distributed / production

---

## License

Apache-2.0
