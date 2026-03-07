from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Optional

import psycopg

from settlement.models import Case, CaseState, OutcomeSignal


class PostgresStore:
    """
    Minimal Postgres-backed durable store.

    Mirrors the same JSON-serialization approach as SQLiteStore:
    - one row per case
    - full case payload stored as JSON text
    - survives process restarts
    - intended as the first production backend step

    This is intentionally simple:
    - no connection pool yet
    - no advisory locks yet
    - no normalized signals table yet
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _connect(self):
        return psycopg.connect(self.dsn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cases (
                        case_id TEXT PRIMARY KEY,
                        updated_at DOUBLE PRECISION NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
            conn.commit()

    def _case_to_json(self, case: Case) -> str:
        d = asdict(case)

        # normalize enum -> string
        d["state"] = str(case.state.value if hasattr(case.state, "value") else case.state)

        # normalize signals -> plain dict
        sigs = {}
        for k, v in (case.signals or {}).items():
            if hasattr(v, "__dataclass_fields__"):
                sigs[k] = asdict(v)
            else:
                sigs[k] = v
        d["signals"] = sigs

        return json.dumps(d, separators=(",", ":"), sort_keys=True)

    def _json_to_case(self, payload_json: str) -> Case:
        d = json.loads(payload_json)

        # rebuild signals
        sigs = {}
        for k, sd in (d.get("signals") or {}).items():
            if isinstance(sd, dict):
                sigs[k] = OutcomeSignal(**sd)
        d["signals"] = sigs

        # rebuild state enum
        st = d.get("state", "OPEN")
        d["state"] = CaseState(st)

        return Case(**d)

    def get_case(self, case_id: str) -> Optional[Case]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload_json FROM cases WHERE case_id = %s",
                    (case_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return self._json_to_case(row[0])

    def put_case(self, case: Case) -> None:
        payload = self._case_to_json(case)
        now = time.time()

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cases (case_id, updated_at, payload_json)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (case_id) DO UPDATE SET
                        updated_at = EXCLUDED.updated_at,
                        payload_json = EXCLUDED.payload_json
                    """,
                    (case.case_id, now, payload),
                )
            conn.commit()