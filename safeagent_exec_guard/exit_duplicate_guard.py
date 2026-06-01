"""
ExitDuplicateGuardRuntime
=========================
Exit-side exactly-once guard for SafeAgent.
Wires into the SQLite store already used for entry-side claims.

Key structure:
  exit:{symbol}:{qty}:{entry_ts}   -> exit claim row (PENDING / COMMITTED / QUARANTINED)
  phantom:{symbol}                 -> quarantine flag (cleared on new-turn entry)

Drop this file into: C:\SAFEAGENT\safeagent_exec_guard\exit_duplicate_guard.py
"""

import sqlite3
import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("safeagent.exit_guard")


class ExitDuplicateGuardRuntime:
    """
    Exit-side idempotency guard backed by the existing SafeAgent SQLite store.

    Usage in exit_qty() ~line 912 of the bot:

        guard = ExitDuplicateGuardRuntime(db_path="C:/trading/rv_etf_bot/safeagent_orders.db")
        result = guard.claim_exit(symbol=symbol, qty=qty, entry_ts=entry_ts)
        if result == "SKIP":
            logger.info(f"SAFEAGENT EXIT SKIP {symbol} qty={qty}")
            return 0
        # ... proceed to broker call ...
        # after broker responds:
        if broker_status == 422:
            guard.mark_quarantined(symbol=symbol, qty=qty, entry_ts=entry_ts)
        else:
            guard.mark_committed(symbol=symbol, qty=qty, entry_ts=entry_ts)
            guard.clear_phantom(symbol)
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self):
        """Create exit_claims table if it doesn't exist."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exit_claims (
                    request_id   TEXT PRIMARY KEY,
                    symbol       TEXT NOT NULL,
                    qty          REAL NOT NULL,
                    entry_ts     TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'PENDING',
                    created_at   REAL NOT NULL,
                    updated_at   REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS phantom_positions (
                    symbol       TEXT PRIMARY KEY,
                    quarantined  INTEGER NOT NULL DEFAULT 1,
                    updated_at   REAL NOT NULL
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim_exit(self, symbol: str, qty: float, entry_ts: str) -> str:
        """
        Attempt to claim an exit slot.

        Returns:
            "PROCEED"  — first caller, safe to send to broker
            "SKIP"     — duplicate, guard already has this exit
        """
        request_id = self._make_key(symbol, qty, entry_ts)
        now = time.time()

        with self._conn() as conn:
            existing = conn.execute(
                "SELECT status FROM exit_claims WHERE request_id = ?",
                (request_id,)
            ).fetchone()

            if existing:
                logger.warning(
                    f"SAFEAGENT EXIT DUPLICATE BLOCKED | "
                    f"request_id={request_id} status={existing[0]}"
                )
                return "SKIP"

            conn.execute(
                """INSERT INTO exit_claims
                   (request_id, symbol, qty, entry_ts, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'PENDING', ?, ?)""",
                (request_id, symbol, qty, entry_ts, now, now)
            )
            conn.commit()

        logger.info(f"SAFEAGENT EXIT CLAIMED | request_id={request_id}")
        return "PROCEED"

    def mark_committed(self, symbol: str, qty: float, entry_ts: str):
        """Mark exit as successfully committed after broker 200."""
        self._update_status(symbol, qty, entry_ts, "COMMITTED")

    def mark_quarantined(self, symbol: str, qty: float, entry_ts: str):
        """
        Mark exit as quarantined after broker 422 terminal.
        Also sets phantom position flag for the symbol.
        """
        self._update_status(symbol, qty, entry_ts, "QUARANTINED")
        self._set_phantom(symbol)
        logger.warning(
            f"SAFEAGENT PHANTOM SET | symbol={symbol} — "
            f"422 terminal, position quarantined"
        )

    def is_entry_blocked(self, symbol: str) -> bool:
        """
        Assert 2: phantom position does NOT block new-turn entry.
        Always returns False — phantom state is informational only.
        New-turn entries proceed regardless of phantom flag.
        """
        phantom = self._get_phantom(symbol)
        if phantom:
            logger.info(
                f"SAFEAGENT PHANTOM PRESENT | symbol={symbol} — "
                f"entry NOT blocked (phantom is informational)"
            )
        return False  # Never block entry

    def clear_phantom(self, symbol: str):
        """Clear phantom flag after successful new-turn entry."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM phantom_positions WHERE symbol = ?",
                (symbol,)
            )
            conn.commit()
        logger.info(f"SAFEAGENT PHANTOM CLEARED | symbol={symbol}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(symbol: str, qty: float, entry_ts: str) -> str:
        return f"exit:{symbol}:{qty}:{entry_ts}"

    def _update_status(self, symbol: str, qty: float, entry_ts: str, status: str):
        request_id = self._make_key(symbol, qty, entry_ts)
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE exit_claims SET status = ?, updated_at = ? WHERE request_id = ?",
                (status, now, request_id)
            )
            conn.commit()
        logger.info(f"SAFEAGENT EXIT {status} | request_id={request_id}")

    def _set_phantom(self, symbol: str):
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO phantom_positions (symbol, quarantined, updated_at)
                   VALUES (?, 1, ?)
                   ON CONFLICT(symbol) DO UPDATE SET quarantined=1, updated_at=excluded.updated_at""",
                (symbol, now)
            )
            conn.commit()

    def _get_phantom(self, symbol: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT quarantined FROM phantom_positions WHERE symbol = ?",
                (symbol,)
            ).fetchone()
        return bool(row and row[0])

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            yield conn
        finally:
            conn.close()
