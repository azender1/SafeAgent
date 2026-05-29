# SafeAgent

> Deterministic execution for AI agents interacting with real-world systems.

---

## The Problem

Agents don’t fail on decisions.  
They fail on **uncertain completion**.

Timeout → retry → duplicate execution.

---

## The Fix

SafeAgent introduces an **execution boundary**:

- prevents duplicate side effects  
- resolves retries against prior attempts  
- enforces exactly-once outcomes  

---

## Example

### Without SafeAgent

```
BUY QQQ 2 @ 355.10
timeout
retry
BUY QQQ 2 @ 355.10   <-- duplicate

Position: 4 shares
Capital: $1420.40
```

### With SafeAgent

```
BUY QQQ 2 @ 355.10
timeout
retry
SafeAgent: returning cached result

Position: 2 shares
Capital: $710.20
```

---

## Core Idea

```
Agent → SafeAgent → Real World
```

Retries don’t replay.  
They **resolve**.

---

## Why It Matters

Irreversible actions:
- trades
- payments
- bookings

cannot rely on “retry until success”.

---

## Status

Early reference implementation.

---

## Demo

Same retry. One doubles your position. One doesn’t.
