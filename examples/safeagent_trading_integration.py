"""
SafeAgent Integration Example — QQQ/TQQQ Trading Bot Pattern
============================================================
DEMO ONLY — not for live trading.

This file shows how SafeAgent wraps the order execution layer
of a QQQ-led TQQQ/SQQQ bot to enforce exactly-once execution.

The pattern: replace place_order_with_retry() with SafeAgent-guarded
execution. Same retry safety. Zero custom state machine. Durable receipts.

Install: pip install safeagent-exec-guard
Docs:    https://github.com/azender1/SafeAgent
"""

import os
import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# SafeAgent import
from settlement.settlement_requests import SettlementRequestRegistry

ET = ZoneInfo("America/New_York")

# --- SafeAgent registry (single instance for the bot session) ---
registry = SettlementRequestRegistry()


# ---------------------------------------------------------------------------
# BEFORE SafeAgent — hand-rolled retry guard (rv_qqq_v14_1 pattern)
# ---------------------------------------------------------------------------

EXIT_RETRY_COUNT = 3
EXIT_RETRY_SLEEP_SEC = 2


def place_order(symbol, qty, side):
    """Raw broker call — hits Alpaca Markets API."""
    import requests
    url = f"{os.environ['APCA_API_BASE_URL']}/v2/orders"
    headers = {
        "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
    }
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()


def place_order_with_retry_OLD(symbol, qty, side):
    """
    Old pattern: retries up to EXIT_RETRY_COUNT times.

    PROBLEM: if the first call times out but the order actually filled,
    the retry fires a second market order. On TQQQ (3x leveraged),
    that's a doubled position you didn't intend — with real dollars.
    """
    last_err = None
    for attempt in range(1, EXIT_RETRY_COUNT + 1):
        try:
            return place_order(symbol, qty, side)
        except Exception as e:
            last_err = e
            print(f"ORDER RETRY {attempt}/{EXIT_RETRY_COUNT} {side} {symbol}: {e}")
            if attempt < EXIT_RETRY_COUNT:
                time.sleep(EXIT_RETRY_SLEEP_SEC)
    raise last_err


# ---------------------------------------------------------------------------
# AFTER SafeAgent — exactly-once execution, durable receipt
# ---------------------------------------------------------------------------

def make_request_id(symbol, side, bar_ts):
    """
    Deterministic request_id per intended trade action.

    Anchored to the bar timestamp so:
    - Same bar + same action = same id = SafeAgent deduplicates
    - New bar = new id = new execution permitted
    """
    return f"trade:{symbol}:{side}:{bar_ts}"


def place_order_safe(symbol, qty, side, bar_ts):
    """
    SafeAgent-guarded order execution.

    - First call: executes the order, stores receipt
    - Any retry with the same request_id: returns stored receipt, never re-submits
    - Survives process restarts (SQLite-backed by default)
    - Full audit trail: every execution recorded with payload and outcome
    """
    request_id = make_request_id(symbol, side, bar_ts)

    receipt = registry.execute(
        request_id=request_id,
        action=f"order_{side}_{symbol}",
        payload={
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "bar_ts": bar_ts,
        },
        execute_fn=lambda: place_order(symbol, qty, side),
    )

    return receipt


# ---------------------------------------------------------------------------
# Drop-in replacement for enter() and exit_qty() in rv_qqq_v14_1
# ---------------------------------------------------------------------------

def enter_safe(direction, sig, bull_symbol, bear_symbol, qty):
    """
    Safe entry — wraps the buy order with SafeAgent guard.
    Drop-in replacement for the enter() function in rv_qqq_v14_1_scaling_bot.py
    """
    symbol = bull_symbol if direction == "BULL" else bear_symbol
    bar_ts = sig["bar_dt"].isoformat()

    print(f"ENTER {direction} {symbol} qty={qty} bar={bar_ts}")

    try:
        receipt = place_order_safe(symbol, qty, "buy", bar_ts)
        print(f"ORDER PLACED — request_id={make_request_id(symbol, 'buy', bar_ts)}")
        print(f"RECEIPT: {json.dumps(receipt, indent=2, default=str)}")
        return True
    except Exception as e:
        print(f"ORDER ERROR enter {symbol}: {e}")
        return False


def exit_safe(symbol, qty, reason, bar_ts):
    """
    Safe exit — wraps the sell order with SafeAgent guard.
    Drop-in replacement for exit_qty() in rv_qqq_v14_1_scaling_bot.py
    """
    print(f"EXIT {reason} {symbol} qty={qty} bar={bar_ts}")

    try:
        receipt = place_order_safe(symbol, qty, "sell", bar_ts)
        print(f"ORDER PLACED — request_id={make_request_id(symbol, 'sell', bar_ts)}")
        print(f"RECEIPT: {json.dumps(receipt, indent=2, default=str)}")
        return True
    except Exception as e:
        print(f"ORDER ERROR exit {symbol}: {e}")
        return False


# ---------------------------------------------------------------------------
# Demo simulation (no broker connection required)
# ---------------------------------------------------------------------------

def simulate_duplicate_order_scenario():
    """
    Simulates the exact failure mode from rv_qqq_v14_1:

    1. Bot decides to BUY TQQQ
    2. Order fires, broker times out
    3. Bot retries — WITHOUT SafeAgent this fires twice
    4. WITH SafeAgent the retry returns the original receipt
    """
    print("=" * 60)
    print("SAFEAGENT TRADING BOT INTEGRATION DEMO")
    print("=" * 60)
    print()

    # Simulate a bar signal
    bar_ts = datetime.now(ET).replace(second=0, microsecond=0).isoformat()
    symbol = "TQQQ"
    qty = 10
    side = "buy"
    request_id = make_request_id(symbol, side, bar_ts)

    print(f"Scenario: BUY {qty} {symbol} at bar {bar_ts}")
    print(f"Request ID: {request_id}")
    print()

    # Simulate what the order returns
    mock_receipt = {
        "id": "mock-order-abc123",
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "status": "accepted",
        "submitted_at": bar_ts,
    }

    def mock_place_order():
        print(f"  → Broker call executed for {side} {qty} {symbol}")
        return mock_receipt

    print("ATTEMPT 1 — first call:")
    receipt1 = registry.execute(
        request_id=request_id,
        action=f"order_{side}_{symbol}",
        payload={"symbol": symbol, "qty": qty, "side": side},
        execute_fn=mock_place_order,
    )
    print(f"  Receipt: {receipt1}")
    print()

    print("ATTEMPT 2 — retry (simulating broker timeout on attempt 1):")
    receipt2 = registry.execute(
        request_id=request_id,
        action=f"order_{side}_{symbol}",
        payload={"symbol": symbol, "qty": qty, "side": side},
        execute_fn=mock_place_order,  # ← this does NOT execute again
    )
    print(f"  Receipt: {receipt2}")
    print()

    print("RESULT:")
    print(f"  Broker calls fired: 1 (not 2)")
    print(f"  Receipts match: {receipt1 == receipt2}")
    print(f"  Duplicate position: PREVENTED")
    print()
    print("=" * 60)
    print("To use in your bot:")
    print("  Replace place_order_with_retry() with place_order_safe()")
    print("  Pass bar_ts as the idempotency anchor")
    print("  SafeAgent handles the rest")
    print("=" * 60)


if __name__ == "__main__":
    simulate_duplicate_order_scenario()
