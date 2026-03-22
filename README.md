# SafeAgent — Exactly-Once Execution Guard for AI Agents (Prevent Duplicate Payments, Emails, Trades)

AI agents retry.

Retries can duplicate irreversible actions:
payments, emails, trades, tickets.

SafeAgent is a lightweight execution guard that guarantees a side effect runs exactly once — even if the agent, network, or system retries.

It records a durable execution receipt and returns the original result on replay instead of executing again.

> Even if your system runs it twice, it executes once.

---

## The problem

Retries don’t mean “nothing happened.”

They mean “we don’t know what happened.”

So your system tries again.

→ duplicate payments  
→ duplicate trades  
→ duplicate emails  
→ duplicate side effects  

---

## The guarantee

Even if your system runs it twice,  
**it executes once.**

---

## Demo

A retry without protection can execute the same irreversible action twice.

SafeAgent guarantees the side effect runs once — even if the agent retries.

### Retry without protection → duplicate execution ❌
### Retry with SafeAgent → executes once and blocks duplicate ✅

![Before vs After](assets/before_after_demo.gif)

---

## How it works

```python
if store.insert_if_not_exists(request_id, action):
    result = do_side_effect()
    store.complete(request_id, result)
else:
    return "Already executed"
```

A request is identified once.  
Every retry resolves to that same execution.

---

## Where this matters

- payments  
- trading systems  
- background jobs  
- webhooks  
- external API mutations  
- AI agent tool calls  

Any system where running twice is unacceptable.

---

## Install

```bash
pip install safeagent-exec-guard
```

---

## Backends

- SQLite → local / single process  
- Postgres → distributed / production  

---

## Mental model

Without SafeAgent:

1. request sent  
2. timeout or uncertain result  
3. system retries  
4. action runs again  

With SafeAgent:

1. request sent with request_id  
2. execution is recorded  
3. action runs once  
4. retries are ignored or resolved  

---

## One line

SafeAgent stops systems from executing the same action twice when they retry.

---

## License

MIT
