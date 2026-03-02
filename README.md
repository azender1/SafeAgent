# SafeAgent

Deterministic execution guard for AI agents.

Prevents duplicate, replayed, or premature irreversible actions triggered by LLM-based agents by enforcing:

- request-id (nonce) deduplication
- confidence / consensus thresholds before finality
- deterministic state transitions
- exactly-once settlement/execution semantics
- durable state persistence (SQLite)

This repository is a reference implementation and pattern demo (not a hosted service).

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

## Architecture (control plane)

Agent decision / signals  
  ↓  
Reconciliation (conflict detection & containment)  
  ↓  
Finality Gate (blocks execution unless FINAL)  
  ↓  
Execution (exactly-once / idempotent)  
  ↓  
Receipt / Log / Downstream system  

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
- Case state and signals can be persisted to SQLite.
- State survives restarts.
- ACID durability for single-node safety.

### Request-id (nonce) deduplication
- Settlement/execution attempts require a unique `request_id`.
- Replays using the same request_id return the cached result.
- New request_ids after settlement resolve to the same settlement_id.
- Prevents duplicate effects across retries or multiple actors.

### Confidence / consensus threshold policy
- Auto-finalize outcomes when agreement exceeds a threshold (e.g. 80%).
- Falls back to majority decision when threshold is not met (in demo).

---

# Demos (run these)

## 1) SafeAgent demo (duplicate execution prevention)

Run:

```bash
python examples/safe_agent_demo.py
```

Shows:
- agent proposes the same action twice
- request-id dedup blocks duplicate execution
- durable state example (optional)

---

## 2) AI outcome simulation (stochastic agent signals)

Run:

```bash
python examples/simulate_ai.py
```

Shows:
- multiple agents produce stochastic/conflicting outcome signals
- confidence threshold auto-finalizes when agreement is high
- finality gate blocks premature execution
- exactly-once settlement

---

## 3) Prediction-market style demo (inspiration / example domain)

Run:

```bash
python examples/prediction_market_demo.py
```

Shows:
- stake → external resolution signals → finality gate → payout receipt

---

## 4) Persistence demo (prove restart safety)

Run twice:

```bash
python examples/persist_demo.py
python examples/persist_demo.py
```

Second run loads case state from disk.

---

## 5) Nonce dedup demo

Run:

```bash
python examples/nonce_demo.py
```

Shows:
- replay same request_id → dedup
- new request_id after settlement → returns same settlement_id

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
examples/prediction_market_demo.py     prediction market demo
examples/persist_demo.py               persistence demo
examples/nonce_demo.py                 nonce demo
```

---

## Origin / inspiration

This pattern was originally motivated by settlement integrity problems in high-liability systems (payout workflows, oracle-resolved systems, and agent-driven execution).

The same control-plane approach applies broadly to production AI agents that must not double-execute irreversible actions.

---

## Need this in production?

If you’re implementing this pattern in a production agent system and want help tailoring reconciliation rules, execution receipts, or persistence/concurrency strategy, see `LICENSING.md`.

---

## License

Apache-2.0