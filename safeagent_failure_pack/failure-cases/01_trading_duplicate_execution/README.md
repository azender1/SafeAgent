# Trading Duplicate Execution

## Scenario

A trading bot submits an order:

BUY QQQ 2 @ 355.10

The broker times out before confirmation is received.

The system retries.

---

## Failure

The retry submits the same order again.

Result:

- Position: 2 → 4 shares  
- Capital: $710.20 → $1420.40  

Duplicate execution occurred.

---

## Why It Happens

The system cannot determine if the first request succeeded.

Retry assumes failure.

Replay becomes a second side effect.

---

## Without SafeAgent

SUBMIT ORDER
timeout
retry
SUBMIT ORDER   <-- duplicate

---

## With SafeAgent

SUBMIT ORDER
timeout
retry
SafeAgent: returning cached result

---

## Result

- No duplicate execution  
- Deterministic outcome  
- Exactly-once behavior enforced  

---

## Challenge

If you can produce a duplicate execution under this model:

Open an issue or reach out.
