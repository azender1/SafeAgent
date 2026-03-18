import json
import psycopg
from psycopg.rows import dict_row


class PostgresExecutionStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self):
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