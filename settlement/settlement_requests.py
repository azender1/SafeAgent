from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional

from settlement.gate import SettlementError, attempt_settlement
from settlement.models import Case
from safeagent_exec_guard.postgres_store import PostgresExecutionStore
from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore


# ----------------------------
# Settlement (existing behavior)
# ----------------------------

@dataclass
class SettlementRequestResult:
    ok: bool
    settlement_id: Optional[str]
    reason: str


# ----------------------------
# Generic "safe execute" receipts
# ----------------------------

@dataclass
class SafeExecuteReceipt:
    ok: bool
    reason: str
    request_id: str
    execution_id: Optional[str]
    action: str
    payload: Dict[str, Any]
    timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SettlementRequestRegistry:
    """
    Request-id (nonce) dedup layer.

    Supports TWO use-cases:

    A) Settlement dedup (existing behavior)
       - submit(case, request_id) -> SettlementRequestResult
       - First time request_id is seen for a FINAL case, it attempts settlement.
       - Re-using the same request_id returns the same settlement_id (dedup).
       - A different request_id after settlement returns the existing settlement_id.

    B) Generic safe execution for ANY irreversible action (agent tool calls)
       - execute(request_id, action, payload, execute_fn) -> dict receipt
       - First time request_id is seen: runs execute_fn once and records a receipt.
       - Replays with same request_id: returns the original receipt (NO side effects).

    Optional Postgres backing:
       - pass postgres_dsn to persist execution receipts beyond process memory
    """

    def __init__(
        self,
        postgres_dsn: Optional[str] = None,
        sqlite_path: Optional[str] = None,
        pending_ttl_seconds: float = 300.0,
    ) -> None:
        # Settlement dedup
        self._settlement_requests: Dict[str, str] = {}
        self._settlement_created_at: Dict[str, float] = {}

        # Generic safe-execute dedup (in-memory fallback)
        self._exec_receipts: Dict[str, SafeExecuteReceipt] = {}
        self._exec_created_at: Dict[str, float] = {}

        # SQLite two-phase claim store (preferred local durable backend)
        self.sqlite: Optional[SQLiteExecutionStore] = None
        if sqlite_path is not None:
            self.sqlite = SQLiteExecutionStore(
                sqlite_path, pending_ttl_seconds=pending_ttl_seconds
            )

        # Optional Postgres durable store (existing, backward-compatible path)
        self.pg: Optional[PostgresExecutionStore] = None
        if postgres_dsn:
            self.pg = PostgresExecutionStore(postgres_dsn)
            try:
                self.pg.init_db()
            except Exception:
                # Keep registry usable even if Postgres is temporarily unavailable
                self.pg = None

    def _now_utc(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _invoke_execute_fn(self, execute_fn: Callable[..., Any], payload: Dict[str, Any]) -> Any:
        """
        Prefer the modern contract: execute_fn(payload).

        Backward compatibility:
        - if execute_fn takes no parameters, call execute_fn()
        - otherwise call execute_fn(payload)
        """
        sig = inspect.signature(execute_fn)
        if len(sig.parameters) == 0:
            return execute_fn()
        return execute_fn(payload)

    # ---------
    # A) SETTLEMENT
    # ---------

    def submit(self, case: Case, request_id: str) -> SettlementRequestResult:
        if not request_id or not request_id.strip():
            return SettlementRequestResult(False, None, "missing_request_id")

        # If we've seen this exact request before, return cached result.
        if request_id in self._settlement_requests:
            return SettlementRequestResult(
                True,
                self._settlement_requests[request_id],
                "dedup_same_request_id",
            )

        # If already settled, return existing settlement id (dedup across request ids).
        if getattr(case, "settlement_id", None):
            sid = case.settlement_id
            self._settlement_requests[request_id] = sid
            self._settlement_created_at[request_id] = time.time()
            return SettlementRequestResult(True, sid, "already_settled")

        # Otherwise attempt settlement through the existing gate.
        try:
            sid = attempt_settlement(case)
        except SettlementError as e:
            return SettlementRequestResult(False, None, f"settlement_blocked:{e}")

        self._settlement_requests[request_id] = sid
        self._settlement_created_at[request_id] = time.time()
        return SettlementRequestResult(True, sid, "settled")

    # ---------
    # B) GENERIC SAFE EXECUTION (drop-in wrapper)
    # ---------

    def execute(
        self,
        *,
        request_id: str,
        action: str,
        payload: Dict[str, Any],
        execute_fn: Callable[..., Any],
    ) -> Dict[str, Any]:
        """
        At-most-once execution guard for ANY irreversible action.

        - On first call with a request_id: runs execute_fn once, stores a receipt, returns it.
        - On replay with the same request_id: returns the original receipt, does NOT re-run execute_fn.

        Preferred execute_fn shape:
            def fn(payload: dict) -> Any

        Backward compatible with:
            def fn() -> Any
        """

        clean_payload = payload or {}

        if not request_id or not request_id.strip():
            return SafeExecuteReceipt(
                ok=False,
                reason="missing_request_id",
                request_id=request_id or "",
                execution_id=None,
                action=action,
                payload=clean_payload,
                timestamp_utc=self._now_utc(),
            ).to_dict()

        # 0) SQLite two-phase claim path (local durable store)
        if self.sqlite:
            return self._execute_sqlite(request_id, action, clean_payload, execute_fn)

        # 1) Postgres-backed path (durable)
        if self.pg:
            existing = self.pg.get(request_id)
            if existing:
                return SafeExecuteReceipt(
                    ok=existing.get("status") == "completed",
                    reason="dedup_same_request_id",
                    request_id=request_id,
                    execution_id=existing.get("execution_id"),
                    action=existing.get("action") or action,
                    payload=clean_payload,
                    timestamp_utc=self._now_utc(),
                ).to_dict()

            inserted = self.pg.insert_if_not_exists(request_id, action)
            if not inserted:
                existing = self.pg.get(request_id)
                return SafeExecuteReceipt(
                    ok=(existing or {}).get("status") == "completed",
                    reason="dedup_same_request_id",
                    request_id=request_id,
                    execution_id=(existing or {}).get("execution_id"),
                    action=(existing or {}).get("action") or action,
                    payload=clean_payload,
                    timestamp_utc=self._now_utc(),
                ).to_dict()

            try:
                _ = self._invoke_execute_fn(execute_fn, clean_payload)
                exec_id = str(uuid.uuid4())

                receipt = SafeExecuteReceipt(
                    ok=True,
                    reason="executed",
                    request_id=request_id,
                    execution_id=exec_id,
                    action=action,
                    payload=clean_payload,
                    timestamp_utc=self._now_utc(),
                )
                self.pg.complete(request_id, receipt.to_dict())
                return receipt.to_dict()

            except Exception as e:
                receipt = SafeExecuteReceipt(
                    ok=False,
                    reason=f"execute_failed:{type(e).__name__}",
                    request_id=request_id,
                    execution_id=None,
                    action=action,
                    payload=clean_payload,
                    timestamp_utc=self._now_utc(),
                )
                self.pg.complete(request_id, receipt.to_dict())
                return receipt.to_dict()

        # 2) In-memory fallback path
        # (reached only when neither SQLite nor Postgres is configured)
        if request_id in self._exec_receipts:
            r = self._exec_receipts[request_id]
            return SafeExecuteReceipt(
                ok=r.ok,
                reason="dedup_same_request_id",
                request_id=r.request_id,
                execution_id=r.execution_id,
                action=r.action,
                payload=r.payload,
                timestamp_utc=self._now_utc(),
            ).to_dict()

        try:
            _ = self._invoke_execute_fn(execute_fn, clean_payload)
            exec_id = str(uuid.uuid4())
            receipt = SafeExecuteReceipt(
                ok=True,
                reason="executed",
                request_id=request_id,
                execution_id=exec_id,
                action=action,
                payload=clean_payload,
                timestamp_utc=self._now_utc(),
            )
        except Exception as e:
            receipt = SafeExecuteReceipt(
                ok=False,
                reason=f"execute_failed:{type(e).__name__}",
                request_id=request_id,
                execution_id=None,
                action=action,
                payload=clean_payload,
                timestamp_utc=self._now_utc(),
            )

        self._exec_receipts[request_id] = receipt
        self._exec_created_at[request_id] = time.time()
        return receipt.to_dict()

    # ---------
    # SQLite two-phase claim helpers
    # ---------

    def _execute_sqlite(
        self,
        request_id: str,
        action: str,
        clean_payload: Dict[str, Any],
        execute_fn: Callable[..., Any],
    ) -> Dict[str, Any]:
        """Two-phase claim execution backed by SQLiteExecutionStore."""
        existing = self.sqlite.get(request_id)  # type: ignore[union-attr]
        if existing is not None:
            if existing["status"] == "COMMITTED":
                stored = existing.get("result") or {}
                return SafeExecuteReceipt(
                    ok=stored.get("ok", False),
                    reason="dedup_same_request_id",
                    request_id=request_id,
                    execution_id=stored.get("execution_id"),
                    action=stored.get("action") or action,
                    payload=clean_payload,
                    timestamp_utc=self._now_utc(),
                ).to_dict()
            # PENDING: in-flight or a prior crash — caller should retry after TTL
            return SafeExecuteReceipt(
                ok=False,
                reason="claim_pending",
                request_id=request_id,
                execution_id=None,
                action=action,
                payload=clean_payload,
                timestamp_utc=self._now_utc(),
            ).to_dict()

        # Phase 1: claim — atomic INSERT of PENDING row
        if not self.sqlite.claim(request_id, action):  # type: ignore[union-attr]
            # Lost the INSERT race; return current state
            existing = self.sqlite.get(request_id)  # type: ignore[union-attr]
            if existing and existing["status"] == "COMMITTED":
                stored = existing.get("result") or {}
                return SafeExecuteReceipt(
                    ok=stored.get("ok", False),
                    reason="dedup_same_request_id",
                    request_id=request_id,
                    execution_id=stored.get("execution_id"),
                    action=stored.get("action") or action,
                    payload=clean_payload,
                    timestamp_utc=self._now_utc(),
                ).to_dict()
            return SafeExecuteReceipt(
                ok=False,
                reason="claim_pending",
                request_id=request_id,
                execution_id=None,
                action=action,
                payload=clean_payload,
                timestamp_utc=self._now_utc(),
            ).to_dict()

        # Execute the side-effecting function
        try:
            _ = self._invoke_execute_fn(execute_fn, clean_payload)
            exec_id = str(uuid.uuid4())
            receipt = SafeExecuteReceipt(
                ok=True,
                reason="executed",
                request_id=request_id,
                execution_id=exec_id,
                action=action,
                payload=clean_payload,
                timestamp_utc=self._now_utc(),
            )
        except Exception as e:
            receipt = SafeExecuteReceipt(
                ok=False,
                reason=f"execute_failed:{type(e).__name__}",
                request_id=request_id,
                execution_id=None,
                action=action,
                payload=clean_payload,
                timestamp_utc=self._now_utc(),
            )

        # Phase 2: settle — PENDING → COMMITTED (called even on fn failure)
        # A crash before this line leaves the row PENDING; the sweeper will reset it.
        self.sqlite.settle(request_id, receipt.to_dict())  # type: ignore[union-attr]
        return receipt.to_dict()

    def sweep_pending(self) -> int:
        """
        Reset stale PENDING executions to CLAIMABLE.

        Delegates to the SQLiteExecutionStore when configured; returns 0
        otherwise.  Intended to be called periodically (e.g. a cron job or
        background thread) so that requests stranded by a crash can be
        retried once their TTL expires.
        """
        if self.sqlite:
            return self.sqlite.sweep_stale_pending()
        return 0