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
            # Governance columns — idempotent via ADD COLUMN IF NOT EXISTS
            conn.execute("""
                ALTER TABLE execution_requests
                    ADD COLUMN IF NOT EXISTS gov_envelope_hash   TEXT,
                    ADD COLUMN IF NOT EXISTS gov_canonical_bytes TEXT,
                    ADD COLUMN IF NOT EXISTS gov_signature       TEXT,
                    ADD COLUMN IF NOT EXISTS gov_verifier_pubkey TEXT,
                    ADD COLUMN IF NOT EXISTS gov_ots_proof_hex   TEXT,
                    ADD COLUMN IF NOT EXISTS gov_ots_confirmed   BOOLEAN DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS gov_ots_block_time  TEXT,
                    ADD COLUMN IF NOT EXISTS gov_mycelium_trail_id TEXT,
                    ADD COLUMN IF NOT EXISTS gov_mycelium_block_time BIGINT,
                    ADD COLUMN IF NOT EXISTS gov_mycelium_tx_hash TEXT,
                    ADD COLUMN IF NOT EXISTS gov_mycelium_precedence BOOLEAN DEFAULT FALSE
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

    def attach_governance(
        self,
        request_id: str,
        envelope_hash: str,
        canonical_bytes: str,
        signature: str,
        verifier_pubkey: str,
        ots_proof_hex: Optional[str] = None,
        ots_confirmed: bool = False,
        ots_block_time: Optional[str] = None,
    ) -> None:
        """Persist BIP-340 signed governance receipt + OTS proof to the claim row."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE execution_requests
                SET gov_envelope_hash   = %s,
                    gov_canonical_bytes = %s,
                    gov_signature       = %s,
                    gov_verifier_pubkey = %s,
                    gov_ots_proof_hex   = %s,
                    gov_ots_confirmed   = %s,
                    gov_ots_block_time  = %s
                WHERE request_id = %s
                """,
                (
                    envelope_hash,
                    canonical_bytes,
                    signature,
                    verifier_pubkey,
                    ots_proof_hex,
                    ots_confirmed,
                    ots_block_time,
                    request_id,
                ),
            )
            conn.commit()

    def get_submitted_unconfirmed_claims(self) -> list:
        """Return all claims with OTS proof submitted but not yet Bitcoin-confirmed."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT request_id, gov_envelope_hash, gov_ots_proof_hex,
                       gov_ots_confirmed, gov_ots_block_time
                FROM execution_requests
                WHERE gov_ots_proof_hex IS NOT NULL
                  AND (gov_ots_confirmed IS NULL OR gov_ots_confirmed = FALSE)
                ORDER BY claimed_at ASC
                LIMIT 100
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def confirm_governance(
        self,
        request_id: str,
        block_time: str,
    ) -> None:
        """Mark a claim's OTS proof as Bitcoin-confirmed with block time."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE execution_requests
                SET gov_ots_confirmed  = TRUE,
                    gov_ots_block_time = %s
                WHERE request_id = %s
                """,
                (block_time, request_id),
            )
            conn.commit()

    def update_anchor_mycelium(
        self,
        request_id: str,
        trail_id: str,
        block_time: Optional[int] = None,
        tx_hash: Optional[str] = None,
        precedence: bool = False,
    ) -> None:
        """Persist Mycelium on-chain anchor data to the claim row."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE execution_requests
                SET gov_mycelium_trail_id    = %s,
                    gov_mycelium_block_time  = %s,
                    gov_mycelium_tx_hash     = %s,
                    gov_mycelium_precedence  = %s
                WHERE request_id = %s
                """,
                (trail_id, block_time, tx_hash, precedence, request_id),
            )
            conn.commit()

    def get_mycelium_anchor(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Return Mycelium anchor fields for a claim."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT gov_mycelium_trail_id, gov_mycelium_block_time,
                       gov_mycelium_tx_hash, gov_mycelium_precedence
                FROM execution_requests
                WHERE request_id = %s
                """,
                (request_id,),
            ).fetchone()
        if row is None or not row.get("gov_mycelium_trail_id"):
            return None
        return {
            "trail_id":   row["gov_mycelium_trail_id"],
            "block_time": row["gov_mycelium_block_time"],
            "tx_hash":    row["gov_mycelium_tx_hash"],
            "precedence": row["gov_mycelium_precedence"] or False,
        }

    def get_governance(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Return governance fields for a claim, or None if not present."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT gov_envelope_hash, gov_canonical_bytes,
                       gov_signature, gov_verifier_pubkey,
                       gov_ots_proof_hex, gov_ots_confirmed,
                       gov_ots_block_time
                FROM execution_requests
                WHERE request_id = %s
                """,
                (request_id,),
            ).fetchone()
        if row is None or not row.get("gov_envelope_hash"):
            return None
        return {
            "envelope_hash":   row["gov_envelope_hash"],
            "canonical_bytes": row["gov_canonical_bytes"],
            "signature":       row["gov_signature"],
            "verifier_pubkey": row["gov_verifier_pubkey"],
            "ots_proof_hex":   row["gov_ots_proof_hex"],
            "ots_confirmed":   row["gov_ots_confirmed"] or False,
            "ots_block_time":  row["gov_ots_block_time"],
        }
