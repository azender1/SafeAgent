"""SafeAgent boundary adapter for Visa/Cybersource Intelligent Commerce sandbox.

This module deliberately keeps credentials and MLE material outside SafeAgent.
The transport object is responsible for Cybersource authentication, request/response
MLE, and HTTP. SafeAgent owns the execution-control boundary:

- validate the logical action before dispatch;
- consume one permit before the provider call;
- durably correlate the SafeAgent permit with Visa instruction/correlation IDs;
- preserve PENDING_RECONCILIATION when the provider response is lost;
- never mint fresh authority for a retry.

Initial scope: Intelligent Commerce "confirm transaction events" in the sandbox.
The provider endpoint documented by Cybersource is:

    POST https://apitest.cybersource.com/acp/v1/instructions/{instructionId}/confirmations

No production endpoint is enabled by this module.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .boundary import ActionRequest, BoundaryGateway, DispatchReceipt, PermitDenied, canonical_bytes


CYBERSOURCE_CONFIRM_TRANSACTION = "cybersource.intelligent_commerce.confirm_transaction"
CYBERSOURCE_TEST_BASE_URL = "https://apitest.cybersource.com"


class CybersourceIntelligentCommerceTransport(Protocol):
    """Credential-bearing provider transport supplied by the caller."""

    def confirm_transaction(self, instruction_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class CybersourceObservation:
    permit_id: str
    state: str
    source: str
    instruction_id: str
    client_correlation_id: str
    transaction_id: str | None
    provider_status: str | None
    last_error: str | None = None


def _provider_state(status: str | None) -> str:
    if status == "COMPLETED":
        return "CONFIRMED"
    if status in {"DECLINED", "REJECTED", "CANCELLED", "CANCELED"}:
        return "REJECTED"
    if status in {"PENDING", "PROCESSING"}:
        return "OPEN"
    return "UNCERTAIN"


class SQLiteCybersourceStore:
    """Durable provider correlation for one SafeAgent-controlled Visa action."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(Path(path))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cybersource_operations (
                    permit_id TEXT PRIMARY KEY,
                    instruction_id TEXT NOT NULL,
                    client_correlation_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    transaction_id TEXT,
                    provider_status TEXT,
                    reconciliation_state TEXT NOT NULL,
                    boundary_decision TEXT,
                    last_source TEXT,
                    last_error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cybersource_instruction
                    ON cybersource_operations(instruction_id);
                CREATE INDEX IF NOT EXISTS idx_cybersource_correlation
                    ON cybersource_operations(client_correlation_id);
                """
            )

    def prepare(
        self,
        permit_id: str,
        instruction_id: str,
        client_correlation_id: str,
        payload: Mapping[str, Any],
        *,
        now: int,
    ) -> None:
        request_json = canonical_bytes(dict(payload)).decode("utf-8")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT instruction_id, client_correlation_id, request_json "
                "FROM cybersource_operations WHERE permit_id=?",
                (permit_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO cybersource_operations
                    (permit_id,instruction_id,client_correlation_id,request_json,
                     reconciliation_state,created_at,updated_at)
                    VALUES (?,?,?,?, 'PREPARED', ?, ?)""",
                    (permit_id, instruction_id, client_correlation_id, request_json, now, now),
                )
            elif (
                row["instruction_id"] != instruction_id
                or row["client_correlation_id"] != client_correlation_id
                or row["request_json"] != request_json
            ):
                conn.execute("ROLLBACK")
                raise PermitDenied("cybersource_operation_collision")
            conn.execute("COMMIT")

    def record_observation(
        self,
        permit_id: str,
        response: Mapping[str, Any],
        *,
        source: str,
        now: int,
    ) -> CybersourceObservation:
        operation = self.get(permit_id)
        if operation is None:
            raise ValueError("unknown SafeAgent permit correlation")
        status = response.get("status")
        transaction_id = response.get("transactionId")
        response_correlation = response.get("clientCorrelationId")
        if status is not None and not isinstance(status, str):
            raise ValueError("Cybersource status must be a string")
        if transaction_id is not None and not isinstance(transaction_id, str):
            raise ValueError("Cybersource transactionId must be a string")
        if response_correlation is not None and response_correlation != operation["client_correlation_id"]:
            raise ValueError("Cybersource clientCorrelationId mismatch")
        state = _provider_state(status)
        with self._connect() as conn:
            conn.execute(
                """UPDATE cybersource_operations
                SET transaction_id=?, provider_status=?, reconciliation_state=?,
                    last_source=?, last_error=NULL, updated_at=?
                WHERE permit_id=?""",
                (transaction_id, status, state, source, now, permit_id),
            )
        return CybersourceObservation(
            permit_id=permit_id,
            state=state,
            source=source,
            instruction_id=operation["instruction_id"],
            client_correlation_id=operation["client_correlation_id"],
            transaction_id=transaction_id,
            provider_status=status,
        )

    def record_boundary_decision(self, permit_id: str, decision: str, *, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cybersource_operations SET boundary_decision=?, updated_at=? WHERE permit_id=?",
                (decision, now, permit_id),
            )

    def record_error(self, permit_id: str, error: str, *, source: str, now: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE cybersource_operations
                SET reconciliation_state='UNCERTAIN', last_source=?, last_error=?, updated_at=?
                WHERE permit_id=?""",
                (source, error[:1000], now, permit_id),
            )

    def get(self, permit_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cybersource_operations WHERE permit_id=?", (permit_id,)
            ).fetchone()
        return dict(row) if row else None


class CybersourceConfirmTransactionGateway:
    """Permit-gated Intelligent Commerce confirmation call.

    This first adapter intentionally does not invent a provider readback API.
    If a response is lost, SafeAgent preserves PENDING_RECONCILIATION and the
    operation remains correlated by permit_id, instruction_id, and
    clientCorrelationId for operator/provider reconciliation.
    """

    def __init__(
        self,
        boundary: BoundaryGateway,
        transport: CybersourceIntelligentCommerceTransport,
        store: SQLiteCybersourceStore,
    ):
        self.boundary = boundary
        self.transport = transport
        self.store = store

    @staticmethod
    def _validate(request: ActionRequest) -> tuple[str, str, dict[str, Any]]:
        if request.action != CYBERSOURCE_CONFIRM_TRANSACTION:
            raise PermitDenied("unsupported_cybersource_action")
        if not isinstance(request.payload, Mapping):
            raise PermitDenied("invalid_cybersource_payload")
        payload = dict(request.payload)
        instruction_id = payload.pop("instruction_id", None)
        if not isinstance(instruction_id, str) or not instruction_id.strip():
            raise PermitDenied("missing_cybersource_instruction_id")
        correlation = payload.get("clientCorrelationId")
        if not isinstance(correlation, str) or not correlation.strip():
            raise PermitDenied("missing_cybersource_client_correlation_id")
        transaction_data = payload.get("transactionData")
        if not isinstance(transaction_data, list) or not transaction_data:
            raise PermitDenied("missing_cybersource_transaction_data")
        if any(not isinstance(item, Mapping) for item in transaction_data):
            raise PermitDenied("invalid_cybersource_transaction_data")
        forbidden = {"shared_secret", "api_key", "key_id", "private_key", "p12", "password"}
        if forbidden.intersection(payload):
            raise PermitDenied("credential_material_not_allowed_in_payload")
        return instruction_id.strip(), correlation.strip(), payload

    def dispatch(
        self, token: str, request: ActionRequest, *, now: int | None = None
    ) -> DispatchReceipt:
        now = int(time.time()) if now is None else int(now)
        instruction_id, correlation, provider_payload = self._validate(request)
        claims = self.boundary._authorize(token, request, now)
        self.store.prepare(
            claims.permit_id,
            instruction_id,
            correlation,
            provider_payload,
            now=now,
        )

        def send(_: ActionRequest) -> dict[str, Any]:
            try:
                response = dict(
                    self.transport.confirm_transaction(instruction_id, provider_payload)
                )
            except Exception as exc:
                self.store.record_error(
                    claims.permit_id, str(exc), source="confirm_transaction", now=now
                )
                raise
            self.store.record_observation(
                claims.permit_id,
                response,
                source="confirm_response",
                now=now,
            )
            return response

        receipt = self.boundary.dispatch(token, request, send, now=now)
        self.store.record_boundary_decision(claims.permit_id, receipt.decision, now=now)
        return receipt

    def ingest_provider_observation(
        self,
        permit_id: str,
        response: Mapping[str, Any],
        *,
        source: str = "operator_readback",
        now: int | None = None,
    ) -> CybersourceObservation:
        """Attach an independently obtained provider observation.

        This does not dispatch or retry the external action. It is the explicit
        reconciliation path until a documented Cybersource readback endpoint is
        available for this action.
        """
        now = int(time.time()) if now is None else int(now)
        return self.store.record_observation(permit_id, response, source=source, now=now)


__all__ = [
    "CYBERSOURCE_CONFIRM_TRANSACTION",
    "CYBERSOURCE_TEST_BASE_URL",
    "CybersourceConfirmTransactionGateway",
    "CybersourceIntelligentCommerceTransport",
    "CybersourceObservation",
    "SQLiteCybersourceStore",
]
