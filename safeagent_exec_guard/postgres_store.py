import json

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # optional dependency for local/in-memory demos
    psycopg = None
    dict_row = None


class PostgresExecutionStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self):
        if psycopg is None:
            raise RuntimeError("psycopg is not installed. Install psycopg[binary] to use the PostgresExecutionStore.")
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def init_db(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS execution_requests (
                    request_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result JSONB,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
                """)
                # Governance columns — safe to run on existing tables
                cur.execute("""
                ALTER TABLE execution_requests
                    ADD COLUMN IF NOT EXISTS gov_envelope_hash TEXT,
                    ADD COLUMN IF NOT EXISTS gov_canonical_bytes TEXT,
                    ADD COLUMN IF NOT EXISTS gov_signature TEXT,
                    ADD COLUMN IF NOT EXISTS gov_verifier_pubkey TEXT,
                    ADD COLUMN IF NOT EXISTS gov_ots_proof_hex TEXT,
                    ADD COLUMN IF NOT EXISTS gov_ots_confirmed BOOLEAN DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS gov_ots_block_time TEXT;
                """)
            conn.commit()

    def get(self, request_id: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM execution_requests WHERE request_id = %s",
                    (request_id,)
                )
                return cur.fetchone()

    def insert_if_not_exists(self, request_id: str, action: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO execution_requests (request_id, action, status)
                    VALUES (%s, %s, 'pending')
                    ON CONFLICT (request_id) DO NOTHING
                """, (request_id, action))
                inserted = cur.rowcount == 1
            conn.commit()
        return inserted

    def complete(self, request_id: str, result):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE execution_requests
                    SET status = 'completed',
                        result = %s,
                        updated_at = NOW()
                    WHERE request_id = %s
                """, (json.dumps(result), request_id))
            conn.commit()

    def attach_governance(
        self,
        request_id: str,
        envelope_hash: str,
        canonical_bytes: str,
        signature: str,
        verifier_pubkey: str,
        ots_proof_hex: str = None,
        ots_confirmed: bool = False,
        ots_block_time: str = None,
    ):
        """Persist governance receipt fields to the execution_requests row."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE execution_requests
                    SET gov_envelope_hash    = %s,
                        gov_canonical_bytes  = %s,
                        gov_signature        = %s,
                        gov_verifier_pubkey  = %s,
                        gov_ots_proof_hex    = %s,
                        gov_ots_confirmed    = %s,
                        gov_ots_block_time   = %s
                    WHERE request_id = %s
                """, (
                    envelope_hash,
                    canonical_bytes,
                    signature,
                    verifier_pubkey,
                    ots_proof_hex,
                    ots_confirmed,
                    ots_block_time,
                    request_id,
                ))
            conn.commit()

    def get_governance(self, request_id: str):
        """Return governance fields for a claim, or None if not present."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT gov_envelope_hash, gov_canonical_bytes,
                           gov_signature, gov_verifier_pubkey,
                           gov_ots_proof_hex, gov_ots_confirmed,
                           gov_ots_block_time
                    FROM execution_requests
                    WHERE request_id = %s
                """, (request_id,))
                row = cur.fetchone()
                if not row or not row.get("gov_envelope_hash"):
                    return None
                return {
                    "envelope_hash":    row["gov_envelope_hash"],
                    "canonical_bytes":  row["gov_canonical_bytes"],
                    "signature":        row["gov_signature"],
                    "verifier_pubkey":  row["gov_verifier_pubkey"],
                    "ots_proof_hex":    row["gov_ots_proof_hex"],
                    "ots_confirmed":    row["gov_ots_confirmed"] or False,
                    "ots_block_time":   row["gov_ots_block_time"],
                }
