"""
Tests for the two-phase claim pattern.

Covers:
- SQLiteExecutionStore unit tests (claim, settle, sweeper, crash simulation)
- SettlementRequestRegistry integration with SQLite backend
- Backward-compatibility: in-memory path unchanged
"""
from __future__ import annotations

import time

import pytest

from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore
from settlement.settlement_requests import SettlementRequestRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(ttl: float = 60.0) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(":memory:", pending_ttl_seconds=ttl)


# ---------------------------------------------------------------------------
# SQLiteExecutionStore — claim
# ---------------------------------------------------------------------------

class TestClaim:
    def test_claim_new_inserts_pending(self):
        store = _store()
        assert store.claim("r1", "email.send") is True
        row = store.get("r1")
        assert row is not None
        assert row["status"] == "PENDING"
        assert row["result"] is None
        assert row["committed_at"] is None
        assert row["claimed_at"] > 0

    def test_claim_duplicate_returns_false(self):
        store = _store()
        store.claim("r2", "action")
        assert store.claim("r2", "action") is False

    def test_claim_after_committed_returns_false(self):
        store = _store()
        store.claim("r3", "action")
        store.settle("r3", {"ok": True})
        assert store.claim("r3", "action") is False

    def test_get_nonexistent_returns_none(self):
        store = _store()
        assert store.get("nonexistent") is None

    def test_different_request_ids_are_independent(self):
        store = _store()
        assert store.claim("a", "x") is True
        assert store.claim("b", "x") is True
        assert store.get("a")["status"] == "PENDING"
        assert store.get("b")["status"] == "PENDING"


# ---------------------------------------------------------------------------
# SQLiteExecutionStore — settle
# ---------------------------------------------------------------------------

class TestSettle:
    def test_settle_transitions_to_committed(self):
        store = _store()
        store.claim("s1", "webhook")
        result = {"ok": True, "execution_id": "abc-123"}
        store.settle("s1", result)
        row = store.get("s1")
        assert row["status"] == "COMMITTED"
        assert row["result"] == result
        assert row["committed_at"] is not None

    def test_settle_is_idempotent_on_committed(self):
        store = _store()
        store.claim("s2", "action")
        store.settle("s2", {"ok": True, "execution_id": "first"})
        store.settle("s2", {"ok": True, "execution_id": "second"})  # no-op
        row = store.get("s2")
        assert row["result"]["execution_id"] == "first"

    def test_result_json_round_trips(self):
        store = _store()
        store.claim("s3", "action")
        original = {"ok": True, "nested": {"key": [1, 2, 3]}, "none_val": None}
        store.settle("s3", original)
        assert store.get("s3")["result"] == original


# ---------------------------------------------------------------------------
# SQLiteExecutionStore — sweeper
# ---------------------------------------------------------------------------

class TestSweeper:
    def test_sweeper_removes_stale_pending(self):
        store = SQLiteExecutionStore(":memory:", pending_ttl_seconds=0.01)
        store.claim("stale", "action")
        time.sleep(0.05)
        assert store.sweep_stale_pending() == 1
        assert store.get("stale") is None

    def test_sweeper_leaves_fresh_pending(self):
        store = _store(ttl=60.0)
        store.claim("fresh", "action")
        assert store.sweep_stale_pending() == 0
        assert store.get("fresh") is not None

    def test_sweeper_leaves_committed_alone(self):
        store = SQLiteExecutionStore(":memory:", pending_ttl_seconds=0.01)
        store.claim("done", "action")
        store.settle("done", {"ok": True})
        time.sleep(0.05)
        assert store.sweep_stale_pending() == 0
        assert store.get("done")["status"] == "COMMITTED"

    def test_swept_request_becomes_reclaimable(self):
        store = SQLiteExecutionStore(":memory:", pending_ttl_seconds=0.01)
        store.claim("retry", "action")
        time.sleep(0.05)
        store.sweep_stale_pending()
        assert store.get("retry") is None
        assert store.claim("retry", "action") is True

    def test_sweeper_returns_count(self):
        store = SQLiteExecutionStore(":memory:", pending_ttl_seconds=0.01)
        for i in range(3):
            store.claim(f"batch-{i}", "action")
        time.sleep(0.05)
        assert store.sweep_stale_pending() == 3

    def test_configurable_ttl_respected(self):
        store = SQLiteExecutionStore(":memory:", pending_ttl_seconds=1000.0)
        store.claim("long-ttl", "action")
        assert store.sweep_stale_pending() == 0


# ---------------------------------------------------------------------------
# Crash simulation
# ---------------------------------------------------------------------------

class TestCrashSafety:
    def test_crash_leaves_pending_not_committed(self):
        """Simulate a process crash by claiming but never calling settle."""
        store = _store()
        store.claim("crash-req", "critical.action")

        # Simulate crash — settle() is never called.
        row = store.get("crash-req")
        assert row["status"] == "PENDING"
        assert row["result"] is None

    def test_pending_blocks_concurrent_claim(self):
        """While PENDING, a second concurrent caller cannot claim — no double-execution."""
        store = _store()
        assert store.claim("dup", "action") is True
        assert store.claim("dup", "action") is False

    def test_sweeper_clears_crash_residue_and_allows_retry(self):
        store = SQLiteExecutionStore(":memory:", pending_ttl_seconds=0.01)
        store.claim("crashed", "action")

        # Crash: settle never called.  TTL expires.
        time.sleep(0.05)
        swept = store.sweep_stale_pending()
        assert swept == 1

        # Safe to re-claim and re-execute.
        assert store.claim("crashed", "action") is True


# ---------------------------------------------------------------------------
# SettlementRequestRegistry — SQLite integration
# ---------------------------------------------------------------------------

class TestRegistrySQLite:
    def setup_method(self):
        self.reg = SettlementRequestRegistry(sqlite_path=":memory:")

    def test_first_call_executes_fn(self):
        calls = []

        def fn(payload):
            calls.append(payload)

        receipt = self.reg.execute(
            request_id="exec-1",
            action="email.send",
            payload={"to": "user@example.com"},
            execute_fn=fn,
        )
        assert receipt["ok"] is True
        assert receipt["reason"] == "executed"
        assert len(calls) == 1

    def test_replay_does_not_re_execute(self):
        calls = []

        def fn(payload):
            calls.append(1)

        self.reg.execute(request_id="exec-2", action="a", payload={}, execute_fn=fn)
        r2 = self.reg.execute(request_id="exec-2", action="a", payload={}, execute_fn=fn)
        assert r2["reason"] == "dedup_same_request_id"
        assert len(calls) == 1

    def test_committed_after_success(self):
        self.reg.execute(
            request_id="exec-3",
            action="webhook",
            payload={},
            execute_fn=lambda: None,
        )
        row = self.reg.sqlite.get("exec-3")
        assert row["status"] == "COMMITTED"

    def test_execution_id_preserved_across_dedup(self):
        r1 = self.reg.execute(
            request_id="exec-4", action="a", payload={}, execute_fn=lambda: None
        )
        r2 = self.reg.execute(
            request_id="exec-4", action="a", payload={}, execute_fn=lambda: None
        )
        assert r1["execution_id"] == r2["execution_id"]

    def test_failed_fn_settles_as_committed(self):
        def boom(payload):
            raise ValueError("oops")

        receipt = self.reg.execute(
            request_id="exec-5", action="risky", payload={}, execute_fn=boom
        )
        assert receipt["ok"] is False
        assert "execute_failed" in receipt["reason"]
        row = self.reg.sqlite.get("exec-5")
        # Even on failure the row must reach COMMITTED (error receipt stored)
        assert row["status"] == "COMMITTED"

    def test_failed_fn_deduped_on_replay(self):
        calls = []

        def boom(payload):
            calls.append(1)
            raise RuntimeError("fail")

        self.reg.execute(request_id="exec-6", action="a", payload={}, execute_fn=boom)
        r2 = self.reg.execute(request_id="exec-6", action="a", payload={}, execute_fn=boom)
        assert r2["reason"] == "dedup_same_request_id"
        assert len(calls) == 1

    def test_missing_request_id_returns_error(self):
        receipt = self.reg.execute(
            request_id="", action="a", payload={}, execute_fn=lambda: None
        )
        assert receipt["ok"] is False
        assert receipt["reason"] == "missing_request_id"

    def test_sweep_pending_via_registry(self):
        self.reg.sqlite.pending_ttl_seconds = 0.01
        self.reg.sqlite.claim("manual-pending", "action")
        time.sleep(0.05)
        swept = self.reg.sweep_pending()
        assert swept == 1

    def test_sweep_pending_no_sqlite_returns_zero(self):
        reg_no_sqlite = SettlementRequestRegistry()
        assert reg_no_sqlite.sweep_pending() == 0

    def test_no_fn_call_arg_signature_still_works(self):
        """Backward-compatible: execute_fn() with no parameters."""
        calls = []

        def fn():
            calls.append(1)

        receipt = self.reg.execute(
            request_id="no-arg", action="a", payload={}, execute_fn=fn
        )
        assert receipt["ok"] is True
        assert len(calls) == 1

    def test_pending_ttl_configurable(self):
        reg = SettlementRequestRegistry(sqlite_path=":memory:", pending_ttl_seconds=0.01)
        reg.sqlite.claim("ttl-req", "action")
        time.sleep(0.05)
        assert reg.sweep_pending() == 1


# ---------------------------------------------------------------------------
# Backward compatibility — in-memory path unchanged
# ---------------------------------------------------------------------------

class TestInMemoryBackwardCompat:
    def test_dedup_still_works(self):
        reg = SettlementRequestRegistry()
        calls = []

        def fn(payload):
            calls.append(1)

        reg.execute(request_id="im-1", action="a", payload={}, execute_fn=fn)
        r2 = reg.execute(request_id="im-1", action="a", payload={}, execute_fn=fn)
        assert r2["reason"] == "dedup_same_request_id"
        assert len(calls) == 1

    def test_execute_ok(self):
        reg = SettlementRequestRegistry()
        receipt = reg.execute(
            request_id="im-2",
            action="send",
            payload={"x": 1},
            execute_fn=lambda p: p,
        )
        assert receipt["ok"] is True
        assert receipt["reason"] == "executed"

    def test_no_sqlite_attribute(self):
        reg = SettlementRequestRegistry()
        assert reg.sqlite is None
