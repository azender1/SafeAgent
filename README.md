# SafeAgent

Deterministic execution guard for AI agents.

```bash
pip install safeagent-exec-guard
```

Prevents duplicate, replayed, or premature irreversible actions triggered by LLM-based agents by enforcing:

- request-id (nonce) deduplication
- confidence / consensus thresholds before finality
- deterministic state transitions
- exactly-once settlement/execution semantics
- durable state persistence (SQLite)

This repository is a reference implementation and pattern demo (not a hosted service).

---

## Install

```bash
pip install safeagent-exec-guard
```

Requires Python 3.10+.

---

## Why SafeAgent?

LLM agents frequently retry tool calls or replay events when something fails.

Without a guard layer, this can cause duplicate execution of irreversible actions (tickets, emails, payouts, trades).

### Without SafeAgent

```python
create_support_ticket(customer_id="C123", severity="high")
create_support_ticket(customer_id="C123", severity="high")  # duplicate
```

### With SafeAgent (exactly-once execution)

```python
from settlement.settlement_requests import SettlementRequestRegistry

registry = SettlementRequestRegistry()

receipt = registry.execute(
    request_id="agent_action_123",
    action="create_support_ticket",
    payload={"customer_id": "C123", "severity": "high"}
)

print(receipt)
```

If the agent retries the same `request_id`, SafeAgent returns the **original receipt** instead of executing again.

---

## Quick Example

```python
from settlement.models import Case
from settlement.store import SQLiteStore

store = SQLiteStore("safeagent.db")
case = Case(case_id="example_case")
store.put_case(case)

print("SafeAgent initialized:", case.state)
```

---

## What problem does this solve?

AI agents running in production often:

- retry tool calls on partial failure
- replay webhook events
- execute the same action twice
- act on provisional/ambiguous outcomes
- loop under uncertainty and produce conflicting signals

When agents touch real systems (tickets, emails, DB writes, payouts, trades), duplicate execution becomes expensive.

SafeAgent provides a control-plane pattern that sits between an agent decision and an irreversible action and only allows execution when the outcome is deterministically FINAL.

---

## High-level flow

Agent / Outcome Signals  
→ Reconciliation / Consensus  
→ Finality Gate  
→ Execution (exactly-once)  
→ Audit / Receipt

---

## State machine

OPEN  
→ RESOLVED_PROVISIONAL  
→ IN_RECONCILIATION  
→ FINAL  
→ SETTLED  

- Ambiguous signals transition to IN_RECONCILIATION  
- Execution is impossible unless state is FINAL  
- Execution is replay-safe (idempotent)  
- Late signals are ignored after finality  

---

## Key features

### Durable persistence (SQLiteStore)
- Case state and signals can be persisted to SQLite
- State survives restarts
- ACID durability for single-node safety

### Request-id (nonce) deduplication
- Settlement/execution attempts require a unique `request_id`
- Replays using the same request_id return the cached result
- New request_ids after settlement resolve to the same settlement_id
- Prevents duplicate effects across retries or multiple actors

### Confidence / consensus threshold policy
- Auto-finalize outcomes when agreement exceeds a threshold (e.g. 80%)
- Falls back to majority decision when threshold is not met (in demo)

---

## Demos

### 1) SafeAgent demo (duplicate execution prevention)

```bash
python examples/safe_agent_demo.py
```

### 2) AI outcome simulation (stochastic agent signals)

```bash
python examples/simulate_ai.py
```

### 3) Persistence demo (prove restart safety)

Run twice:

```bash
python examples/persist_demo.py
python examples/persist_demo.py
```

---

## Core implementation structure

```
models.py                      case + signal models
state_machine.py               deterministic transitions
reconciliation.py              conflict detection & resolution
gate.py                        settlement/execution gate
store.py                       in-memory + SQLite persistence

policy.py                      confidence/threshold decision helper
settlement_requests.py         request-id (nonce) dedup wrapper

examples/safe_agent_demo.py            SafeAgent demo
examples/simulate_ai.py                AI demo
examples/persist_demo.py               persistence demo
examples/nonce_demo.py                 nonce demo
```

---

## Need this in production?

If you’re implementing this pattern in a production agent system and want help tailoring reconciliation rules, execution receipts, or persistence/concurrency strategy, see `LICENSING.md`.

---

## License

Apache-2.0
