from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional

from settlement.gate import SettlementError, attempt_settlement
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
       - First time request_id is seen: runs execute_fn once and records a receipt.
       - Replays with same request_id: returns the original receipt (NO side effects).
    """

    def __init__(self) -> None:
        # Settlement dedup
        self._settlement_requests: Dict[str, str] = {}
        self._settlement_created_at: Dict[str, float] = {}

        # Generic safe-execute dedup
        self._exec_receipts: Dict[str, SafeExecuteReceipt] = {}
        self._exec_created_at: Dict[str, float] = {}

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
        Exactly-once execution guard for ANY irreversible action.

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

        # Dedup: same request_id returns the original receipt
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

        # First execution
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