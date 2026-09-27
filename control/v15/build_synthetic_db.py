"""Build the 45-row synthetic SQLite fixture used by Control v15 tests."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "fixtures" / "safeagent_orders_synthetic_sample.csv"
DB_PATH = ROOT / "fixtures" / "safeagent_orders_synthetic_sample.db"


def build() -> Path:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 45:
        raise RuntimeError(f"expected 45 synthetic rows, found {len(rows)}")
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """
            CREATE TABLE orders (
                request_id TEXT PRIMARY KEY,
                result TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        con.executemany(
            "INSERT INTO orders(request_id,result,status,created_at) VALUES (?,?,?,?)",
            [(r["request_id"], r["result"] or None, r["status"], r["created_at"]) for r in rows],
        )
        con.commit()
        count = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    finally:
        con.close()
    if count != 45:
        raise RuntimeError(f"generated SQLite fixture has {count} rows")
    return DB_PATH


if __name__ == "__main__":
    print(build())
