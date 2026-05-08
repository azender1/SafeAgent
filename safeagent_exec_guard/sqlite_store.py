"""
SQLite execution store with two-phase claim semantics.

Status lifecycle
----------------
    CLAIMABLE  (implicit — no row exists)
    -> PENDING   (row inserted; execution is in flight)
    -> COMMITTED (execution finished; result is persisted)

A crash between claim and settle leaves the row PENDING.
sweep_stale_pending() deletes PENDING rows older than pending_ttl_seconds,
returning those request-IDs to CLAIMABLE so they can be retried.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Optional


class SQLiteExecutionStore:
    """
    Two-phase claim execution store backed by SQLite.

    Parameters
    ----------
    db_path:
        File path for the SQLite database, or ``:memory:`` for an
        in-process ephemeral store (useful in tests).
    pending_ttl_seconds:
        How long (in seconds) a PENDING row may exist before the sweeper
        considers it stale and resets it to CLAIMABLE.  Default: 300 s.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        pending_ttl_seconds: float = 300.0,
    ) -> None:
        self.db_path = db_path
        self.pending_ttl_seconds = pending_ttl_seconds
        # Keep a single persistent connection so that :memory: databases
        # survive across method calls (each new sqlite3.connect(":memory:")
        # produces an empty, independent database).
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        return self._conn

    def _init_db(self) -> None:
        conn = self._connect()
        # 1. Ensure base table exists (original schema, no agent_id)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_requests (
                request_id   TEXT PRIMARY KEY,
                action       TEXT NOT NULL,
                status       TEXT NOT NULL
                             CHECK (status IN ('PENDING', 'COMMITTED')),
                result       TEXT,
                claimed_at   REAL NOT NULL,
                committed_at REAL
            )
        """)
        conn.commit()
        # 2. Migrate existing databases to add new columns
        self._migrate_db(conn)
        # 3. Create / ensure indexes (after migration guarantees columns exist)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_exec_status_claimed
            ON execution_requests (status, claimed_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_exec_agent_id
            ON execution_requests (agent_id)
        """)
        conn.commit()

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema (backward-compatible)."""
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(execution_requests)").fetchall()
        }
        if "agent_id" not in existing:
            conn.execute(
                "ALTER TABLE execution_requests ADD COLUMN agent_id TEXT"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim(
        self,
        request_id: str,
        action: str,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Phase 1 — atomically insert a PENDING row.

        Parameters
        ----------
        agent_id:
            EVM wallet address of the caller (extracted from the x402
            payment payload).  ``None`` when payment gating is disabled.

        Returns ``True``  when the row was created (caller owns the claim).
        Returns ``False`` when a row already exists (PENDING or COMMITTED).
        """
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO execution_requests
                    (request_id, action, status, agent_id, claimed_at)
                VALUES (?, ?, 'PENDING', ?, ?)
                """,
                (request_id, action, agent_id, time.time()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            conn.rollback()
            return False

    def settle(self, request_id: str, result: Dict[str, Any]) -> None:
        """
        Phase 2 — transition PENDING → COMMITTED and persist *result*.

        Calling settle on an already-COMMITTED row is a no-op (idempotent).
        """
        conn = self._connect()
        conn.execute(
            """
            UPDATE execution_requests
            SET    status       = 'COMMITTED',
                   result       = ?,
                   committed_at = ?
            WHERE  request_id   = ?
              AND  status       = 'PENDING'
            """,
            (json.dumps(result), time.time(), request_id),
        )
        conn.commit()

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Return the row for *request_id*, or ``None`` when CLAIMABLE (no row).

        The ``result`` field is deserialized from JSON when present.
        """
        row = self._connect().execute(
            "SELECT * FROM execution_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        if out.get("result"):
            out["result"] = json.loads(out["result"])
        return out

    def audit_claims(
        self,
        *,
        agent_id: Optional[str] = None,
        action: Optional[str] = None,
        status: Optional[str] = None,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Return paginated claim history with optional filters.

        Parameters
        ----------
        agent_id:
            Filter by EVM wallet address (exact match).
        action:
            Filter by action name (exact match).
        status:
            Filter by ``'PENDING'`` or ``'COMMITTED'``.
        from_ts:
            Include only rows where ``claimed_at >= from_ts`` (Unix timestamp).
        to_ts:
            Include only rows where ``claimed_at <= to_ts`` (Unix timestamp).
        limit:
            Page size (default 100, capped at 1000).
        offset:
            Pagination offset (default 0).

        Returns a dict ``{"items": [...], "total": N, "limit": L, "offset": O}``.
        """
        limit = min(limit, 1000)
        conditions: list = []
        params: list = []

        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if action is not None:
            conditions.append("action = ?")
            params.append(action)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if from_ts is not None:
            conditions.append("claimed_at >= ?")
            params.append(from_ts)
        if to_ts is not None:
            conditions.append("claimed_at <= ?")
            params.append(to_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        conn = self._connect()

        total: int = conn.execute(
            f"SELECT COUNT(*) FROM execution_requests {where}",
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT * FROM execution_requests
            {where}
            ORDER BY claimed_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        items = []
        for row in rows:
            out = dict(row)
            if out.get("result"):
                out["result"] = json.loads(out["result"])
            items.append(out)

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def sweep_stale_pending(self) -> int:
        """
        Delete PENDING rows whose ``claimed_at`` is older than
        ``pending_ttl_seconds``.

        Swept rows become CLAIMABLE again — a subsequent call may re-claim
        and re-execute the same request_id.

        Returns the count of rows deleted.
        """
        cutoff = time.time() - self.pending_ttl_seconds
        conn = self._connect()
        cur = conn.execute(
            """
            DELETE FROM execution_requests
            WHERE  status     = 'PENDING'
              AND  claimed_at < ?
            """,
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount
