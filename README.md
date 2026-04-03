# SafeAgent / Execution Guard

## Prevent duplicate or incorrect execution when retries happen

A missing execution boundary for AI agents, trading bots, automations, and workflow systems.

When a system hits:
- timeout
- partial failure
- retry
- uncertain completion

…it often does not know whether the action already happened.

That is how you get:
- duplicate trades
- duplicate payments
- duplicate emails
- duplicate API mutations

SafeAgent adds a durable execution boundary around real side effects so retries can be reconciled instead of replayed.

> Most systems can retry. Very few can decide when a retry is still correct.

---

## Demo — duplicate trade prevented after uncertain completion

![SafeAgent Trading Demo](assets/safeagent_trading_demo_v2.gif)

Without SafeAgent:
- retry replays the action
- duplicate trade executes

With SafeAgent:
- retry resolves against existing execution
- duplicate is blocked

---

## Quickstart

pip install safeagent-exec-guard

```python
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

store = SQLiteExecutionStore("safeagent.db")
store.init_db()

def send_payment(request_id: str):
    action = "send_payment"

    if store.insert_if_not_exists(request_id, action):
        result = {"status": "sent"}
        store.complete(request_id, result)
        print("executed")
    else:
        print("duplicate blocked")
```

---

## Mental model

Without SafeAgent:
retry → replay → duplicate

With SafeAgent:
retry → resolve → safe

---

## Where this matters

- trading
- payments
- APIs
- agents
- workflows

---

## License

Apache-2.0
