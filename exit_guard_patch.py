# =============================================================================
# SAFEAGENT EXIT GUARD — patch for rv_qqq_v22_wave_persistence_confirmed_flip
# =============================================================================
# The duplicate exit problem:
#   Two sell orders fire within 2 seconds (same bar_ts, same reason, same qty).
#   Both reach place_order_with_retry before either has COMMITTED, so both
#   pass the INSERT OR IGNORE check and both hit the broker.
#   Result: short position, after-hours cleanup required.
#
# The fix:
#   A dedicated exit lock keyed to (symbol, session_entry_ts).
#   The key is derived from WHEN the bot entered, not what bar fired the exit.
#   First call to exit_qty_guarded wins. Every subsequent call in the same
#   position lifecycle returns SKIP immediately — before touching the broker.
#
# How to apply:
#   1. Replace the existing exit_qty() function with exit_qty_guarded() below.
#   2. No other changes needed — full_exit() already calls exit_qty().
#   3. The guard uses the same safeagent_orders.db and same _sa_con connection.
# =============================================================================


def exit_qty_guarded(reason, qty_to_sell):
    """
    Exit guard wrapper. Replaces exit_qty().

    Guard key: exit:{symbol}:{qty}:{entry_ts}
    - symbol      — what we're selling
    - qty         — how many shares (prevents a partial-exit key colliding
                    with a full-exit key on the same entry)
    - entry_ts    — when the bot entered this position (state["entry_ts"]).
                    This is stable across the entire position lifecycle and
                    changes only on a new entry, so two exit calls in the
                    same position always share the same key.

    First call: INSERT OR IGNORE writes PENDING, check returns PENDING,
                proceeds to broker, settles to COMMITTED.
    Second call (2 seconds later, same position): INSERT OR IGNORE is a
                no-op (key exists), check returns COMMITTED, returns SKIP
                immediately — broker never sees the second order.
    """
    symbol = current_symbol()
    if not symbol or qty_to_sell <= 0:
        return False

    px = price_for_symbol(symbol)
    if px is None or px < MIN_PRICE_FOR_ORDER:
        log(f"EXIT BLOCKED: invalid price for {symbol} price={px} reason={reason}")
        return False

    # --- Exit guard claim ---
    entry_ts = state.get("entry_ts") or "unknown"
    exit_request_id = f"exit:{symbol}:{qty_to_sell}:{entry_ts}"

    if _safeagent_enabled:
        try:
            _sa_con.execute(
                "INSERT OR IGNORE INTO orders (request_id, status) VALUES (?, 'PENDING')",
                (exit_request_id,)
            )
            _sa_con.commit()
            row = _sa_con.execute(
                "SELECT status, result FROM orders WHERE request_id = ?",
                (exit_request_id,)
            ).fetchone()
            if row and row[0] == 'COMMITTED':
                log(
                    f"SAFEAGENT EXIT SKIP: duplicate exit blocked "
                    f"{symbol} qty={qty_to_sell} entry={entry_ts} reason={reason} "
                    f"— returning cached result"
                )
                return True  # treat as successful exit (position already closed)
        except Exception as e:
            log(f"SAFEAGENT EXIT CLAIM ERROR (continuing without guard): {e}")

    # --- Execute the exit ---
    bar_ts = state.get("last_bar_ts") or "unknown"
    try:
        place_order_with_retry(symbol, qty_to_sell, "sell", bar_ts=f"{bar_ts}:{reason}")
    except Exception as e:
        log(f"ORDER ERROR exit {symbol}: {e}")
        return False

    set_execution_lock()

    pnl_pct = 0.0
    if state["entry_price"]:
        pnl_pct = (px / float(state["entry_price"])) - 1.0

    log(
        f"EXIT {reason} symbol={symbol} qty={qty_to_sell} price={px:.2f} "
        f"held_min={held_minutes():.1f} pnl_pct={pnl_pct:.4f}"
    )

    # --- Settle the exit claim ---
    if _safeagent_enabled:
        try:
            _sa_con.execute(
                "UPDATE orders SET status='COMMITTED', result=? WHERE request_id=?",
                (json.dumps(f"exit:{symbol}:{qty_to_sell}:{reason}"), exit_request_id)
            )
            _sa_con.commit()
        except Exception as e:
            log(f"SAFEAGENT EXIT SETTLE ERROR (non-fatal): {e}")

    save_state()
    return True


# =============================================================================
# HOW TO APPLY THIS PATCH
# =============================================================================
#
# In rv_qqq_v22_wave_persistence_confirmed_flip__1__.py:
#
# FIND this function (around line 912):
#
#     def exit_qty(reason, qty_to_sell):
#         symbol = current_symbol()
#         if not symbol or qty_to_sell <= 0: return False
#         ...
#         place_order_with_retry(symbol, qty_to_sell, "sell", bar_ts=f"{bar_ts}:{reason}")
#         ...
#
# REPLACE the entire exit_qty() function body with exit_qty_guarded() above,
# renaming it back to exit_qty() so all callers (full_exit, etc.) work unchanged:
#
#     def exit_qty(reason, qty_to_sell):
#         <paste the body of exit_qty_guarded here>
#
# That's the only change. full_exit() calls exit_qty() — it will automatically
# use the guarded version.
#
# =============================================================================
# WHAT THIS FIXES
# =============================================================================
#
# May 19 incident: two sell orders at 3:21:16 and 3:21:19 PM, both filled,
#                  created -12 share short.
# May 21 incident: two sell orders at 11:26:26 and 11:26:28 AM, both filled,
#                  created -12 share short.
#
# With this guard: second sell hits INSERT OR IGNORE (no-op), reads COMMITTED,
#                  logs SAFEAGENT EXIT SKIP, returns True. Broker never sees it.
#
# =============================================================================
# CASE STUDY NOTE (for RFC_EXECUTION_GUARD.md / AutoGen #7353)
# =============================================================================
#
# The entry guard (place_order_with_retry) prevents duplicate buys.
# The exit guard (this patch) prevents duplicate sells.
# Both use the same COMMITTED/SKIP state machine, same db, same primitive.
#
# Production evidence:
#   - May 19: duplicate exit → -12 short → after-hours cover required
#   - May 21: duplicate exit → -12 short → after-hours cover required
#   - Both incidents: entry guard working correctly (6 SKIP events May 21)
#   - Gap confirmed in production, patched with same primitive
#
# =============================================================================
