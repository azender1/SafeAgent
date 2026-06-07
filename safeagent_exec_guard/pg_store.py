"""
PostgreSQL execution store with two-phase claim semantics.
Mirrors SQLiteExecutionStore API exactly — drop-in replacement.

Status lifecycle
----------------
    CLAIMABLE  (implicit — no row exists)
    -> PENDING   (row inserted; execution is in flight)
    -> COMMITTED (execution finished; result is persisted)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

import psycopg
from psycopg.rows import dict_row


class PgExecutionStore:
    """
    Two-phase claim execution store backed by PostgreSQL.
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        *,
        pending_ttl_seconds: float = 300.0,
    ) -> None:
        self.database_url = database_url or os.environ["DATABASE_URL"]
        self.pending_ttl_seconds = pending_ttl_seconds
        self._init_db()

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_requests (
                    request_id   TEXT PRIMARY KEY,
                    action       TEXT NOT NULL,
                    status       TEXT NOT NULL
                                 CHECK (status IN ('PENDING', 'COMMITTED')),
                    result       TEXT,
                    agent_id     TEXT,
                    claimed_at   DOUBLE PRECISION NOT NULL,
                    committed_at DOUBLE PRECISION
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exec_status_claimed
                ON execution_requests (status, claimed_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exec_agent_id
                ON execution_requests (agent_id)
            """)
            conn.commit()

    def claim(
        self,
        request_id: str,
        action: str,
        agent_id: Optional[str] = None,
    ) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO execution_requests
                        (request_id, action, status, agent_id, claimed_at)
                    VALUES (%s, %s, 'PENDING', %s, %s)
                    """,
                    (request_id, action, agent_id, time.time()),
                )
                conn.commit()
            return True
        except Exception:
            return False

    def settle(self, request_id: str, result: Dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE execution_requests
                SET    status       = 'COMMITTED',
                       result       = %s,
                       committed_at = %s
                WHERE  request_id   = %s
                  AND  status       = 'PENDING'
                """,
                (json.dumps(result), time.time(), request_id),
            )
            conn.commit()

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM execution_requests WHERE request_id = %s",
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
        limit = min(limit, 1000)
        conditions: list = []
        params: list = []

        if agent_id is not None:
            conditions.append("agent_id = %s")
            params.append(agent_id)
        if action is not None:
            conditions.append("action = %s")
            params.append(action)
        if status is not None:
            conditions.append("status = %s")
            params.append(status)
        if from_ts is not None:
            conditions.append("claimed_at >= %s")
            params.append(from_ts)
        if to_ts is not None:
            conditions.append("claimed_at <= %s")
            params.append(to_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with self._conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) as cnt FROM execution_requests {where}",
                params,
            ).fetchone()["cnt"]

            rows = conn.execute(
                f"""
                SELECT * FROM execution_requests
                {where}
                ORDER BY claimed_at DESC
                LIMIT %s OFFSET %s
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
        cutoff = time.time() - self.pending_ttl_seconds
        with self._conn() as conn:
            cur = conn.execute(
                """
                DELETE FROM execution_requests
                WHERE  status     = 'PENDING'
                  AND  claimed_at < %s
                """,
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount
