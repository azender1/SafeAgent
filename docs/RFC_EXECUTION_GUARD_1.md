# RFC: Execution Guard for Duplicate Agent Actions
**SafeAgent Production Case Study — May 2026**
**Author:** @azender1
**Repo:** github.com/azender1/SafeAgent
**Status:** Production, patched

---

## Summary

Three identical incidents across three trading sessions (May 19, May 21, May 22) produced duplicate exit orders from an autonomous trading agent. Each incident created a phantom short position requiring manual cleanup. The root cause was the same each time: two bot instances fired an exit order within the same 2-second window, before either had committed to the execution guard database. The fix — a dedicated exit guard keyed to `exit:{symbol}:{qty}:{entry_ts}` — prevents the second call from reaching the broker.

This document is a production case study for the SafeAgent exactly-once execution guard primitive and serves as Consilium substrate for A2A #1734 Candidate 2 (anchor absence).

---

## The Bug

### What happened

An autonomous trading bot (TQQQ momentum strategy) runs two instances simultaneously for redundancy. Both instances share a SafeAgent execution guard database (`safeagent_orders.db`) for entry-side deduplication.

When exit conditions triggered, both instances detected the same signal on the same bar and called `exit_qty()` within 2 seconds of each other. The entry guard (`place_order_with_retry`) was protected. The exit path was not.

Both sell orders reached the broker. The first filled correctly, closing the position. The second filled against a flat account, opening a short. The bot then saw an open position it couldn't explain, fired `ENTRY BLOCKED: broker still shows open positions` for the rest of the session, and went dead.

### Timeline

| Date | Exit fired | Duplicate fired | Result |
|------|-----------|----------------|--------|
| May 19 | ~3:21:16 PM | ~3:21:19 PM | -12 share short, manual cover |
| May 21 | ~11:26:26 AM | ~11:26:28 AM | -12 share short, manual cover |
| May 22 | 09:49:06 AM | 09:49:06 AM | Phantom TQQQ position, bot dead 09:49–15:55 |

Three incidents, same 2-second window, same gap, same outcome.

### Why the entry guard didn't catch it

The entry guard uses `request_id = f"{symbol}:{bar_ts}:{direction}"`. This key is bar-scoped — it changes every bar. It correctly deduplicates entry orders.

The exit path had no guard at all. Both instances called `place_order_with_retry` directly, with no idempotency key, no database check, no COMMITTED/SKIP state machine. The broker received both orders and filled both.

### The 422-cascade (anchor absence)

On May 21, the bot attempted to claim a SafeAgent slot via the Railway-hosted payment endpoint immediately after a position flip. The endpoint returned `422 Unprocessable Entity` on the first call — the anchor was never placed. Subsequent retry calls saw no COMMITTED record (because the first call never wrote one) and proceeded to execute, producing the duplicate.

This is the anchor-absence case: the guard infrastructure exists and is reachable, but the anchor was never written at the critical moment, so the second agent instance had no record to check against. The result is structurally identical to having no guard at all.

This fixture is logged in A2A #1734 as Candidate 2 substrate (anchor absence, distinct from anchor inadmissibility).

---

## The Fix

### Exit guard key

```python
entry_ts = state.get("entry_ts") or "unknown"
exit_request_id = f"exit:{symbol}:{qty_to_sell}:{entry_ts}"
```

**Why `entry_ts` and not `bar_ts`:** `entry_ts` is set when the bot enters a position and does not change until the next entry. It is stable across the entire position lifecycle. Two exit calls in the same position — regardless of which bar triggered them — always share the same key.

`bar_ts` would not work here: both instances are on the same bar, so the key would be identical and both would race to write PENDING before either reads COMMITTED.

### State machine

```
First call:
  INSERT OR IGNORE → writes PENDING (key is new)
  SELECT status    → returns PENDING
  → proceeds to broker, fills, settles to COMMITTED

Second call (2 seconds later, same position):
  INSERT OR IGNORE → no-op (key exists)
  SELECT status    → returns COMMITTED
  → logs SAFEAGENT EXIT SKIP, returns True
  → broker never sees the second order
```

### What the log looks like after the patch

```
09:49:06 EXIT V20_FAST_FLIP symbol=TQQQ qty=24 price=78.36 held_min=14.1 pnl_pct=-0.0018
09:49:06 SAFEAGENT EXIT SKIP: duplicate exit blocked TQQQ qty=24 entry=2026-05-22T09:35:05 reason=V20_FAST_FLIP — returning cached result
```

One fill. No short. Bot continues normally.

### Guard database

```
C:\trading\rv_etf_bot\safeagent_orders.db
```

Same SQLite database used by the entry guard. No new infrastructure required.

---

## Before and After

### Before (unpatched)

```python
def exit_qty(reason, qty_to_sell):
    symbol = current_symbol()
    if not symbol or qty_to_sell <= 0: return False
    px = price_for_symbol(symbol)
    ...
    place_order_with_retry(symbol, qty_to_sell, "sell", bar_ts=f"{bar_ts}:{reason}")
    set_execution_lock()
    ...
    save_state()
    return True
```

No guard. Both instances reach `place_order_with_retry`. Both fill.

### After (patched)

```python
def exit_qty(reason, qty_to_sell):
    symbol = current_symbol()
    if not symbol or qty_to_sell <= 0: return False
    px = price_for_symbol(symbol)
    ...

    # SafeAgent exit guard
    entry_ts = state.get("entry_ts") or "unknown"
    exit_request_id = f"exit:{symbol}:{qty_to_sell}:{entry_ts}"

    if _safeagent_enabled:
        _sa_con.execute(
            "INSERT OR IGNORE INTO orders (request_id, status) VALUES (?, 'PENDING')",
            (exit_request_id,)
        )
        _sa_con.commit()
        row = _sa_con.execute(
            "SELECT status FROM orders WHERE request_id = ?",
            (exit_request_id,)
        ).fetchone()
        if row and row[0] == 'COMMITTED':
            log(f"SAFEAGENT EXIT SKIP: duplicate exit blocked ...")
            return True  # position already closed

    place_order_with_retry(symbol, qty_to_sell, "sell", bar_ts=f"{bar_ts}:{reason}")
    set_execution_lock()
    ...

    # Settle
    if _safeagent_enabled:
        _sa_con.execute(
            "UPDATE orders SET status='COMMITTED', result=? WHERE request_id=?",
            (json.dumps(f"exit:{symbol}:{qty_to_sell}:{reason}"), exit_request_id)
        )
        _sa_con.commit()

    save_state()
    return True
```

---

## Relation to SafeAgent Entry Guard

The entry guard and exit guard are the same primitive applied to both sides of the position lifecycle:

| Side | Guard key | Incidents prevented |
|------|-----------|-------------------|
| Entry | `{symbol}:{bar_ts}:{direction}` | Duplicate buys |
| Exit | `exit:{symbol}:{qty}:{entry_ts}` | Duplicate sells |

Both use the same COMMITTED/SKIP state machine, same SQLite database, same `INSERT OR IGNORE` pattern.

The entry guard was working correctly throughout all three incidents — 6 SKIP events logged on May 21 alone. The exit side was the unguarded gap.

---

## Consilium Substrate Note (A2A #1734 Candidate 2)

This case study is submitted as production evidence for **Candidate 2: live-state admissibility at commit** in the A2A #1734 Consilium pass.

The anchor-absence fixture (May 21 422-cascade) is the specific production event already logged by @aeoess as Candidate 2 substrate. This document provides the full three-incident context:

- **Anchor absence** (May 21): 422 on first claim call, anchor never written, second instance proceeds without guard
- **Race condition** (May 19, May 22): both instances reach the broker before either commits, no anchor inadmissibility — just no anchor

Both are distinct from anchor inadmissibility (where the anchor exists but is rejected). Both result in duplicate execution. The fix is the same: write the anchor atomically before the action, check before proceeding.

**Production evidence:**
- 3 incidents, same gap, same fix
- Entry guard: 23 COMMITTED claims in `safeagent_orders.db`, 0 false positives
- Exit guard: patched May 23, 2026 — first production session May 27, 2026
- PyPI: `safeagent-exec-guard` v0.1.18
- Live endpoint: `https://safeagent-production.up.railway.app`

---

## References

- SafeAgent PyPI: https://pypi.org/project/safeagent-exec-guard/
- Production gist: https://gist.github.com/azender1/b9112b6519c935df4a75cb05cd250e26
- A2A #1734: https://github.com/a2aproject/A2A/discussions/1734
- Guard DB: `C:\trading\rv_etf_bot\safeagent_orders.db`
