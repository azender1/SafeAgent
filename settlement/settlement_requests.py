from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Callable

from settlement.gate import attempt_settlement, SettlementError
from settlement.models import Case


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
       - First time request_id is seen: runs execute_fn() once and records a receipt.
       - Replays with same request_id: returns the original receipt (NO side effects).
       - This is the "drop-in wrapper" that makes SafeAgent 10x easier to adopt.
    """

    def __init__(self) -> None:
        # Settlement dedup
        self._settlement_requests: Dict[str, str] = {}
        self._settlement_created_at: Dict[str, float] = {}

        # Generic safe-execute dedup
        self._exec_receipts: Dict[str, SafeExecuteReceipt] = {}
        self._exec_created_at: Dict[str, float] = {}

    # ---------
    # A) SETTLEMENT
    # ---------

    def submit(self, case: Case, request_id: str) -> SettlementRequestResult:
        if not request_id or not request_id.strip():
            return SettlementRequestResult(False, None, "missing_request_id")

        # If we've seen this exact request before, return cached result.
        if request_id in self._settlement_requests:
            return SettlementRequestResult(True, self._settlement_requests[request_id], "dedup_same_request_id")

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
        execute_fn: Callable[[], Any],
    ) -> Dict[str, Any]:
        """
        Exactly-once execution guard for ANY irreversible action.

        - On first call with a request_id: runs execute_fn() ONCE, stores a receipt, returns it.
        - On replay with the same request_id: returns the original receipt, does NOT re-run execute_fn().

        Returns a dict so your demos can print JSON receipts easily.
        """

        if not request_id or not request_id.strip():
            return SafeExecuteReceipt(
                ok=False,
                reason="missing_request_id",
                request_id=request_id or "",
                execution_id=None,
                action=action,
                payload=payload or {},
                timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ).to_dict()

        # Dedup: same request_id returns the original receipt
        if request_id in self._exec_receipts:
            r = self._exec_receipts[request_id]
            # Preserve "replay" semantics
            return SafeExecuteReceipt(
                ok=r.ok,
                reason="dedup_same_request_id",
                request_id=r.request_id,
                execution_id=r.execution_id,
                action=r.action,
                payload=r.payload,
                timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ).to_dict()

        # First execution
        try:
            _ = execute_fn()
            exec_id = str(uuid.uuid4())
            receipt = SafeExecuteReceipt(
                ok=True,
                reason="executed",
                request_id=request_id,
                execution_id=exec_id,
                action=action,
                payload=payload or {},
                timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception as e:
            receipt = SafeExecuteReceipt(
                ok=False,
                reason=f"execute_failed:{type(e).__name__}",
                request_id=request_id,
                execution_id=None,
                action=action,
                payload=payload or {},
                timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )

        self._exec_receipts[request_id] = receipt
        self._exec_created_at[request_id] = time.time()
        return receipt.to_dict()