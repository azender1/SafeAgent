from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional

from .models import Case, CaseState, OutcomeSignal


# ----------------------------
# In-memory store (existing)
# ----------------------------
@dataclass
class InMemoryStore:
    cases: Dict[str, Case] = field(default_factory=dict)

    def get_case(self, case_id: str) -> Optional[Case]:
        return self.cases.get(case_id)

    def put_case(self, case: Case) -> None:
        self.cases[case.case_id] = case


# ----------------------------
# Durable store (SQLite)
# ----------------------------
class SQLiteStore:
    """
    Minimal durable persistence for the reference implementation.
    Stores the entire Case as JSON in a SQLite file.

    This is NOT a "full DB" (no migrations/HA/sharding/etc) — it just proves
    state survives process restarts.
    """

    def __init__(self, path: str = "settlement.db") -> None:
        self.path = path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # autocommit mode via isolation_level=None; we’ll use explicit transactions anyway
        return sqlite3.connect(self.path)

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    updated_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )

    # ---- serialization helpers ----
    def _case_to_json(self, case: Case) -> str:
        d = asdict(case)
        # normalize enum -> string
        d["state"] = str(case.state.value if hasattr(case.state, "value") else case.state)

        # signals is Dict[str, OutcomeSignal]; ensure nested dataclasses are JSON-safe
        # asdict already converts OutcomeSignal to dict; keep it explicit anyway
        sigs = {}
        for k, v in (case.signals or {}).items():
            sigs[k] = asdict(v) if hasattr(v, "__dataclass_fields__") else v
        d["signals"] = sigs

        return json.dumps(d, separators=(",", ":"), sort_keys=True)

    def _json_to_case(self, payload_json: str) -> Case:
        d = json.loads(payload_json)

        # rebuild signals dict
        sigs = {}
        for k, sd in (d.get("signals") or {}).items():
            if isinstance(sd, dict):
                sigs[k] = OutcomeSignal(**sd)
        d["signals"] = sigs

        # rebuild enum
        st = d.get("state", "OPEN")
        d["state"] = CaseState(st)

        return Case(**d)

    # ---- public API (matches InMemoryStore shape) ----
    def get_case(self, case_id: str) -> Optional[Case]:
        with self._connect() as con:
            row = con.execute(
                "SELECT payload_json FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            if not row:
                return None
            return self._json_to_case(row[0])

    def put_case(self, case: Case) -> None:
        payload = self._case_to_json(case)
        now = time.time()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO cases (case_id, updated_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (case.case_id, now, payload),
            )